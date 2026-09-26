#pragma once
#include <windows.h>
#include <cstdio>
#include <cstdint>

// Crash diagnostics for this isolated process only. No debugger attachment,
// other-process reads, dumps of account data, or exception suppression.
inline wchar_t offline_fault_path[32768]{};
inline std::uintptr_t offline_library_base{};
inline std::size_t offline_library_size{};

inline LONG WINAPI record_offline_fault(EXCEPTION_POINTERS* exception) {
    char output[4096]{};
    int used = sprintf_s(output, "{\"code\":%lu,\"frames\":[", exception->ExceptionRecord->ExceptionCode);
    CONTEXT context = *exception->ContextRecord;
    auto nt = GetModuleHandleW(L"ntdll.dll");
    using Lookup = PRUNTIME_FUNCTION (WINAPI*)(DWORD64, PDWORD64, PUNWIND_HISTORY_TABLE);
    using Unwind = PEXCEPTION_ROUTINE (WINAPI*)(DWORD, DWORD64, DWORD64, PRUNTIME_FUNCTION, PCONTEXT, PVOID*, PDWORD64, PKNONVOLATILE_CONTEXT_POINTERS);
    auto lookup = reinterpret_cast<Lookup>(GetProcAddress(nt, "RtlLookupFunctionEntry"));
    auto unwind = reinterpret_cast<Unwind>(GetProcAddress(nt, "RtlVirtualUnwind"));
    for (int i = 0; i < 12 && context.Rip && used > 0 && used < 3500; ++i) {
        const bool in_game = context.Rip >= offline_library_base && context.Rip - offline_library_base < offline_library_size;
        used += sprintf_s(output + used, sizeof(output) - used, "%s{\"gameRva\":%llu,\"inGameLibrary\":%s}",
                          i ? "," : "", in_game ? context.Rip - offline_library_base : 0ull, in_game ? "true" : "false");
        DWORD64 image_base = 0;
        auto function = lookup ? lookup(context.Rip, &image_base, nullptr) : nullptr;
        if (function && unwind) {
            PVOID handler_data = nullptr;
            DWORD64 frame = 0;
            unwind(0, image_base, context.Rip, function, &context, &handler_data, &frame, nullptr);
        } else {
            SIZE_T bytes = 0;
            if (!ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(context.Rsp), &context.Rip, sizeof(context.Rip), &bytes)
                    || bytes != sizeof(context.Rip)) break;
            context.Rsp += sizeof(context.Rip);
        }
    }
    if (used > 0) used += sprintf_s(output + used, sizeof(output) - used, "]}\n");
    HANDLE file = CreateFileW(offline_fault_path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        DWORD written = 0;
        WriteFile(file, output, static_cast<DWORD>(used), &written, nullptr);
        CloseHandle(file);
    }
    return EXCEPTION_EXECUTE_HANDLER;
}
