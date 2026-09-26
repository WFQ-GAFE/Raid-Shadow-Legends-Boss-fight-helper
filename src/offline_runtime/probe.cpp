// Isolated original-engine runner for the Hydra battle-start forecast.
//
// Modes:
//   json-convert <input-directory>
//       Convert the battle's captured BattleSetup/BattleSettings JSON to the
//       original MessagePack with the game's own serializer.
//   forecast <input-directory> [max-game-turn]
//       Run the captured battle in the original BattleProcessor; player
//       commands come from the external policy over this launcher's stdin/stdout.
//   chimera-forecast <input-directory> [seed]
//       The same for a captured Chimera battle; with a seed, the same team and
//       Chimera under another battle seed (strategy simulation).
//
// The launcher runs the work in a hidden child inside a temporary
// AppContainer with no capabilities, one active process and 2 GB of memory.
// The child proves its restricted token before loading any game library.
// Never pass a live game PID or handle; only this launcher's PID is probed.
#include <windows.h>
#include <userenv.h>
#include <sddl.h>
#include <aclapi.h>
#include <objbase.h>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include "managed_runtime.hpp"
#include "static_data_probe.hpp"
#include "json_to_pack_probe.hpp"
#include "forecast_battle.hpp"
#include "chimera_forecast.hpp"
#include "crash_observation.hpp"

namespace fs = std::filesystem;

struct Handle {
    HANDLE value{};
    ~Handle() { if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value); }
};

fs::path executable() {
    std::vector<wchar_t> value(32768);
    const DWORD size = GetModuleFileNameW(nullptr, value.data(), static_cast<DWORD>(value.size()));
    if (!size || size == value.size()) throw std::runtime_error("executable path unavailable");
    return fs::path(std::wstring(value.data(), size));
}

std::string utf8(const std::wstring& value) {
    int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (!size) throw std::runtime_error("UTF-8 path conversion failed");
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), result.data(), size, nullptr, nullptr);
    return result;
}

std::string base64(const std::vector<unsigned char>& data) {
    constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string output;
    output.reserve((data.size() + 2) / 3 * 4);
    for (std::size_t i = 0; i < data.size(); i += 3) {
        const std::uint32_t chunk = (std::uint32_t(data[i]) << 16) |
            (i + 1 < data.size() ? std::uint32_t(data[i + 1]) << 8 : 0) |
            (i + 2 < data.size() ? std::uint32_t(data[i + 2]) : 0);
        output += alphabet[(chunk >> 18) & 63];
        output += alphabet[(chunk >> 12) & 63];
        output += i + 1 < data.size() ? alphabet[(chunk >> 6) & 63] : '=';
        output += i + 2 < data.size() ? alphabet[chunk & 63] : '=';
    }
    return output;
}

struct Profile {
    PSID sid{};
    std::wstring name;
    fs::path path;
    bool created{};
    ~Profile() { if (sid) FreeSid(sid); if (created) DeleteAppContainerProfile(name.c_str()); }
    void create() {
        name = L"Raid.Offline.Probe." + std::to_wstring(GetCurrentProcessId()) + L"." + std::to_wstring(GetTickCount64());
        HRESULT hr = CreateAppContainerProfile(name.c_str(), L"RAID offline runtime probe", L"Temporary offline compatibility research", nullptr, 0, &sid);
        if (FAILED(hr)) throw std::runtime_error("CreateAppContainerProfile failed: " + std::to_string(hr));
        created = true;
        LPWSTR text = nullptr, folder = nullptr;
        if (!ConvertSidToStringSidW(sid, &text)) throw std::runtime_error("SID conversion failed");
        hr = GetAppContainerFolderPath(text, &folder);
        LocalFree(text);
        if (FAILED(hr)) throw std::runtime_error("AppContainer folder unavailable");
        path = folder;
        CoTaskMemFree(folder);
    }
};

// Several launchers may share one bundle folder (parallel strategy
// simulations). Each adds only its own profile's entry and removes only that
// entry again, under one cross-process lock, so no launcher can undo the
// access another launcher's worker is still using.
struct AclLock {
    HANDLE mutex{};
    AclLock() {
        mutex = CreateMutexW(nullptr, FALSE, L"Local\\RaidOfflineProbe.BundleAcl");
        if (!mutex) throw std::runtime_error("Bundle ACL lock unavailable");
        const DWORD wait = WaitForSingleObject(mutex, 60000);
        if (wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED) {
            CloseHandle(mutex);
            throw std::runtime_error("Bundle ACL lock timed out");
        }
    }
    ~AclLock() {
        ReleaseMutex(mutex);
        CloseHandle(mutex);
    }
};

struct ReadAccess {
    fs::path path;
    PSID sid{};
    bool changed{};
    ~ReadAccess() {
        if (!changed) return;
        try {
            AclLock lock;
            apply(REVOKE_ACCESS);
        } catch (...) {
        }
    }
    DWORD apply(ACCESS_MODE mode) const {
        PSECURITY_DESCRIPTOR descriptor = nullptr;
        PACL current = nullptr;
        DWORD error = GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                                           nullptr, nullptr, &current, nullptr, &descriptor);
        if (error) return error;
        EXPLICIT_ACCESSW entry{};
        entry.grfAccessPermissions = FILE_GENERIC_READ | FILE_GENERIC_EXECUTE;
        entry.grfAccessMode = mode;
        entry.grfInheritance = SUB_CONTAINERS_AND_OBJECTS_INHERIT;
        entry.Trustee.TrusteeForm = TRUSTEE_IS_SID;
        entry.Trustee.TrusteeType = TRUSTEE_IS_UNKNOWN;
        entry.Trustee.ptstrName = static_cast<LPWSTR>(sid);
        PACL updated = nullptr;
        error = SetEntriesInAclW(1, &entry, current, &updated);
        if (!error)
            error = SetNamedSecurityInfoW(const_cast<wchar_t*>(path.c_str()), SE_FILE_OBJECT,
                                          DACL_SECURITY_INFORMATION, nullptr, nullptr, updated, nullptr);
        if (updated) LocalFree(updated);
        LocalFree(descriptor);
        return error;
    }
    void grant(const fs::path& directory, PSID profile_sid) {
        path = directory;
        sid = profile_sid;
        AclLock lock;
        const DWORD error = apply(GRANT_ACCESS);
        if (error) throw std::runtime_error("Cannot grant bundle read access: " + std::to_string(error));
        changed = true;
    }
};

std::string sanitized(std::string message) {
    for (char& c : message) if (c == '"' || c == '\\' || c < ' ') c = '_';
    return message;
}

int worker(const fs::path& report, DWORD launcher_pid, const std::wstring& mode,
           const fs::path& input_directory, const std::wstring& parameter,
           HANDLE policy_input, HANDLE policy_output) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    wcscpy_s(offline_fault_path, (report.parent_path() / L"fault.json").c_str());
    SetUnhandledExceptionFilter(record_offline_fault);
    auto emit = [&](const std::string& fields) {
        std::ofstream output(report, std::ios::trunc);
        output << "{\"schema\":1,\"pid\":" << GetCurrentProcessId() << ',' << fields << "}\n";
        output.flush();
        if (!output) throw std::runtime_error("Cannot write isolated probe report");
    };
    Handle token;
    DWORD size = 0, app_container = 0;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token.value) ||
        !GetTokenInformation(token.value, TokenIsAppContainer, &app_container, sizeof(app_container), &size) || app_container != 1)
        return 20;
    GetTokenInformation(token.value, TokenCapabilities, nullptr, 0, &size);
    std::vector<unsigned char> data(size);
    if (!GetTokenInformation(token.value, TokenCapabilities, data.data(), size, &size)) return 21;
    if (reinterpret_cast<const TOKEN_GROUPS*>(data.data())->GroupCount != 0) return 22;
    Handle parent{OpenProcess(PROCESS_VM_READ, FALSE, launcher_pid)};
    const DWORD parent_error = GetLastError();
    if (parent.value || parent_error != ERROR_ACCESS_DENIED) return 23;
    const bool chimera = mode == L"chimera-forecast";
    const bool forecast = mode == L"forecast" || chimera;
    if (!forecast && mode != L"json-convert") return 24;
    if (forecast && (!policy_input || !policy_output)) return 34;
    const std::string isolated = "\"appContainer\":true,\"capabilityCount\":0,\"parentMemoryAccessDenied\":true,";
    emit(isolated + "\"phase\":\"isolation_verified\"");

    const auto bundle = executable().parent_path();
    // Restrict dependency search to copied local inputs and Windows system DLLs.
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS);
    AddDllDirectory(bundle.c_str());
    emit(isolated + "\"phase\":\"library_loading\"");
    HMODULE library = LoadLibraryExW((bundle / L"GameAssembly.dll").c_str(), nullptr,
                                    LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!library) {
        emit(isolated + "\"phase\":\"load_failed\",\"windowsError\":" + std::to_string(GetLastError()));
        return 25;
    }
    offline_library_base = reinterpret_cast<std::uintptr_t>(library);
    const auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(library);
    const auto pe = reinterpret_cast<const IMAGE_NT_HEADERS64*>(reinterpret_cast<const unsigned char*>(library) + dos->e_lfanew);
    offline_library_size = pe->OptionalHeader.SizeOfImage;
    using SetDirectory = void (*)(const char*);
    using Init = int (*)(const char*);
    using Domain = void* (*)();
    using Assemblies = void** (*)(void*, std::size_t*);
    auto set_data = reinterpret_cast<SetDirectory>(GetProcAddress(library, "il2cpp_set_data_dir"));
    auto set_config = reinterpret_cast<SetDirectory>(GetProcAddress(library, "il2cpp_set_config_dir"));
    auto init = reinterpret_cast<Init>(GetProcAddress(library, "il2cpp_init"));
    auto domain = reinterpret_cast<Domain>(GetProcAddress(library, "il2cpp_domain_get"));
    auto assemblies = reinterpret_cast<Assemblies>(GetProcAddress(library, "il2cpp_domain_get_assemblies"));
    if (!set_data || !set_config || !init || !domain || !assemblies) return 26;
    const auto data_path = utf8((bundle / L"il2cpp_data").wstring());
    const auto config_path = utf8((bundle / L"il2cpp_data" / L"etc").wstring());
    set_data(data_path.c_str());
    set_config(config_path.c_str());
    emit(isolated + "\"phase\":\"runtime_initializing\"");
    const int initialized = init("RaidOfflineResearch");
    void* current = domain();
    std::size_t count = 0;
    if (current) assemblies(current, &count);
    if (!current || !count) return 27;
    emit(isolated + "\"phase\":\"runtime_initialized\",\"initReturn\":" + std::to_string(initialized) +
         ",\"assemblyCount\":" + std::to_string(count));

    std::string stage = "configure_model_logger";
    try {
        ManagedRuntime client(library, current, "Unity.Model.dll");
        client.invoke(client.method(client.klass("Client.Model.Common", "SharedModelLogger"), "Configure", 0), nullptr);
        ManagedRuntime model(library, current);
        if (!forecast) {
            stage = "convert_original_json";
            configure_original_messagepack(model, current);
            const auto packed = original_json_setup_to_pack(model, current, input_directory / L"battle-setup.json");
            const auto settings = original_json_settings_to_pack(model, current, input_directory / L"battle-settings.json");
            constexpr char digits[] = "0123456789abcdef";
            std::string guid_hex;
            for (auto byte : packed.guid_bytes) {
                guid_hex += digits[byte >> 4];
                guid_hex += digits[byte & 15];
            }
            std::ostringstream hero_ids;
            hero_ids << '[';
            for (std::size_t i = 0; i < packed.hero_instance_ids.size(); ++i) {
                if (i) hero_ids << ',';
                hero_ids << packed.hero_instance_ids[i];
            }
            hero_ids << ']';
            emit(isolated + "\"phase\":\"original_setup_messagepack_executed\",\"synthetic\":false"
                 ",\"guidBytesHex\":\"" + guid_hex + "\",\"seed\":" + std::to_string(packed.seed) +
                 ",\"stageId\":" + std::to_string(packed.stage_id) +
                 ",\"teamOwnerId\":" + std::to_string(packed.team_owner_id) +
                 ",\"inventoryHeroIds\":" + hero_ids.str() +
                 ",\"battleSetupBytes\":" + std::to_string(packed.packed.size()) +
                 ",\"battleSetupMessagePackBase64\":\"" + base64(packed.packed) +
                 "\",\"battleSettingsBytes\":" + std::to_string(settings.packed.size()) +
                 ",\"battleSettingsMessagePackBase64\":\"" + base64(settings.packed) +
                 "\",\"activeEngineVersion\":" + std::to_string(settings.active_engine_version) +
                 ",\"warmupBattleRandomCount\":" + std::to_string(settings.warmup_battle_random_count) +
                 ",\"maxTurnsInBattle\":" + std::to_string(settings.max_turns_in_battle));
            return 0;
        }
        void* static_data = load_static_data(model, current, bundle / L"static-data.msgpack", [&](const char* step) {
            stage = step;
            emit(isolated + "\"phase\":\"static_data_loading\",\"stage\":\"" + step + "\"");
        });
        PolicyChannel policy(policy_input, policy_output);
        auto progress = [&](const char* step) {
            stage = step;
            emit(isolated + "\"phase\":\"battle_processing\",\"stage\":\"" + step + "\"");
        };
        std::string result;
        if (chimera) {
            int seed = 0;
            const bool override_seed = parameter != L"captured";
            if (override_seed) seed = std::stoi(parameter);
            result = run_chimera_policy_forecast(model, static_data, progress, input_directory / L"battle-setup.msgpack",
                                                 input_directory / L"battle-settings.msgpack",
                                                 override_seed ? &seed : nullptr, policy);
        } else {
            result = run_policy_forecast(model, static_data, progress, input_directory / L"battle-setup.msgpack",
                                         input_directory / L"battle-settings.msgpack", std::stoi(parameter), policy);
        }
        emit(isolated + "\"phase\":\"policy_forecast_executed\"," + result);
        return 0;
    } catch (const std::exception& error) {
        emit(isolated + "\"phase\":\"" + (forecast ? "captured_setup_battle_failed" : "original_setup_messagepack_failed") +
             "\",\"stage\":\"" + sanitized(stage) + "\",\"error\":\"" + sanitized(error.what()) + "\"");
        return forecast ? 31 : 33;
    }
}

int launch(const std::wstring& mode, const fs::path& input_argument, const std::wstring& parameter) {
    const bool chimera = mode == L"chimera-forecast";
    const bool forecast = mode == L"forecast" || chimera;
    if (!forecast && mode != L"json-convert") return 2;
    if (chimera) {
        if (parameter != L"captured") {
            std::size_t used = 0;
            const long long seed = std::stoll(parameter, &used);
            if (used != parameter.size() || seed < INT_MIN || seed > INT_MAX)
                throw std::runtime_error("Seed must be a 32-bit integer");
        }
    } else {
        const int max_game_turn = std::stoi(parameter);
        if (max_game_turn < 1 || max_game_turn > 1000)
            throw std::runtime_error("Game turn limit must be between 1 and 1000");
    }
    const auto exe = executable();
    const fs::path input = fs::weakly_canonical(input_argument);
    const std::vector<const wchar_t*> required = forecast
        ? std::vector<const wchar_t*>{L"battle-setup.msgpack", L"battle-settings.msgpack"}
        : std::vector<const wchar_t*>{L"battle-setup.json", L"battle-settings.json"};
    if (!fs::is_directory(input)) throw std::runtime_error("Input directory is missing");
    for (const auto* name : required)
        if (!fs::is_regular_file(input / name)) throw std::runtime_error("Input directory lacks battle setup or settings");
    Profile profile;
    profile.create();
    ReadAccess access;
    access.grant(exe.parent_path(), profile.sid);
    ReadAccess input_access;
    if (fs::weakly_canonical(exe.parent_path()) != input) input_access.grant(input, profile.sid);
    const auto report = profile.path / L"AC" / L"Temp" / L"probe.json";
    fs::create_directories(report.parent_path());

    SECURITY_CAPABILITIES capabilities{};
    capabilities.AppContainerSid = profile.sid;
    // Forecast mode lends the worker exactly two anonymous-pipe ends: the
    // policy requests go to this launcher's stdout, replies come from stdin.
    // Only these duplicates are inheritable and listed; no names are shared.
    Handle policy_input, policy_output;
    HANDLE inherited[2]{};
    if (forecast) {
        const HANDLE self = GetCurrentProcess();
        if (!DuplicateHandle(self, GetStdHandle(STD_INPUT_HANDLE), self, &policy_input.value,
                             GENERIC_READ | SYNCHRONIZE, TRUE, 0) ||
            !DuplicateHandle(self, GetStdHandle(STD_OUTPUT_HANDLE), self, &policy_output.value,
                             GENERIC_WRITE | SYNCHRONIZE, TRUE, 0))
            throw std::runtime_error("Forecast policy pipes are unavailable");
        inherited[0] = policy_input.value;
        inherited[1] = policy_output.value;
    }
    const DWORD attribute_count = forecast ? 2 : 1;
    SIZE_T bytes = 0;
    InitializeProcThreadAttributeList(nullptr, attribute_count, 0, &bytes);
    std::vector<unsigned char> attributes(bytes);
    auto list = reinterpret_cast<LPPROC_THREAD_ATTRIBUTE_LIST>(attributes.data());
    if (!InitializeProcThreadAttributeList(list, attribute_count, 0, &bytes)) return 3;
    struct AttributesCleanup { LPPROC_THREAD_ATTRIBUTE_LIST p; ~AttributesCleanup() { DeleteProcThreadAttributeList(p); } } cleanup{list};
    if (!UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                   &capabilities, sizeof(capabilities), nullptr, nullptr)) return 4;
    if (forecast && !UpdateProcThreadAttribute(list, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                               inherited, sizeof(inherited), nullptr, nullptr)) return 4;
    Handle job{CreateJobObjectW(nullptr, nullptr)};
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_PROCESS_MEMORY;
    limits.BasicLimitInformation.ActiveProcessLimit = 1;
    limits.ProcessMemoryLimit = std::size_t(2) * 1024 * 1024 * 1024;
    if (!job.value || !SetInformationJobObject(job.value, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) return 5;
    STARTUPINFOEXW startup{};
    startup.StartupInfo.cb = sizeof(startup);
    startup.StartupInfo.dwFlags = STARTF_USESHOWWINDOW | (forecast ? STARTF_USESTDHANDLES : 0);
    startup.StartupInfo.wShowWindow = SW_HIDE;
    startup.lpAttributeList = list;
    PROCESS_INFORMATION process{};
    std::wstring command = L"\"" + exe.wstring() + L"\" --worker \"" + report.wstring() + L"\" " +
                           std::to_wstring(GetCurrentProcessId()) + L" " + mode + L" \"" + input.wstring() +
                           L"\" " + parameter;
    if (forecast)
        command += L" " + std::to_wstring(reinterpret_cast<std::uintptr_t>(policy_input.value)) +
                   L" " + std::to_wstring(reinterpret_cast<std::uintptr_t>(policy_output.value));
    if (!CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, forecast ? TRUE : FALSE,
                        EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_SUSPENDED,
                        nullptr, exe.parent_path().c_str(), &startup.StartupInfo, &process))
        throw std::runtime_error("CreateProcess AppContainer failed: " + std::to_string(GetLastError()));
    Handle child{process.hProcess}, thread{process.hThread};
    // The worker now owns its copies; closing ours lets a dead worker surface
    // as a broken pipe to the policy process instead of a hang.
    if (forecast) {
        CloseHandle(policy_input.value);
        CloseHandle(policy_output.value);
        policy_input.value = policy_output.value = nullptr;
    }
    if (!AssignProcessToJobObject(job.value, child.value)) { TerminateProcess(child.value, 28); return 6; }
    ResumeThread(thread.value);
    const DWORD deadline_ms = forecast ? 300000 : 30000;
    const auto worker_started_at = GetTickCount64();
    const DWORD wait = WaitForSingleObject(child.value, deadline_ms);
    if (wait != WAIT_OBJECT_0) { TerminateJobObject(job.value, 29); WaitForSingleObject(child.value, 5000); }
    DWORD code = 0;
    GetExitCodeProcess(child.value, &code);
    std::ifstream stream(report);
    std::string contents((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    std::ifstream fault_stream(report.parent_path() / L"fault.json");
    std::string fault((std::istreambuf_iterator<char>(fault_stream)), std::istreambuf_iterator<char>());
    // One wrapper line: the worker terminates its report with a newline.
    for (auto* text : {&contents, &fault})
        while (!text->empty() && (text->back() == '\n' || text->back() == '\r' || text->back() == ' '))
            text->pop_back();
    std::cout << "{\"childExitCode\":" << code << ",\"timedOut\":" << (wait == WAIT_TIMEOUT ? "true" : "false")
              << ",\"deadlineMs\":" << deadline_ms << ",\"elapsedMs\":" << (GetTickCount64() - worker_started_at)
              << ",\"observation\":" << (contents.empty() ? "null" : contents)
              << ",\"nativeFault\":" << (fault.empty() ? "null" : fault) << "}\n";
    return code == 0 && wait == WAIT_OBJECT_0 ? 0 : 7;
}

// A GUI-subsystem program has no console. When run by hand from a terminal
// (no redirected handles), borrow that terminal for the JSON output.
void attach_parent_console_if_interactive() {
    const HANDLE output = GetStdHandle(STD_OUTPUT_HANDLE);
    if (output && output != INVALID_HANDLE_VALUE) return;
    if (!AttachConsole(ATTACH_PARENT_PROCESS)) return;
    FILE* stream = nullptr;
    freopen_s(&stream, "CONOUT$", "w", stdout);
    freopen_s(&stream, "CONOUT$", "w", stderr);
    std::ios::sync_with_stdio(true);
}

int wmain(int argc, wchar_t** argv) {
    try {
        if ((argc == 7 || argc == 9) && std::wstring(argv[1]) == L"--worker") {
            HANDLE policy_input = nullptr, policy_output = nullptr;
            if (argc == 9) {
                policy_input = reinterpret_cast<HANDLE>(static_cast<std::uintptr_t>(std::stoull(argv[7])));
                policy_output = reinterpret_cast<HANDLE>(static_cast<std::uintptr_t>(std::stoull(argv[8])));
            }
            return worker(argv[2], std::stoul(argv[3]), argv[4], fs::path(argv[5]), argv[6],
                          policy_input, policy_output);
        }
        attach_parent_console_if_interactive();
        if (argc < 3 || argc > 4) return 1;
        const std::wstring mode = argv[1];
        if (mode == L"json-convert" && argc != 3) return 1;
        const std::wstring parameter = argc == 4 ? std::wstring(argv[3])
                                                 : (mode == L"chimera-forecast" ? L"captured" : L"1000");
        return launch(mode, fs::path(argv[2]), parameter);
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 9;
    }
}
