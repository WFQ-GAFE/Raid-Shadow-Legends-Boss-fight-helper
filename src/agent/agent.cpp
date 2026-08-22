#include "il2cpp_api.hpp"

#include <Windows.h>
#include <MinHook.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <climits>
#include <cstddef>
#include <cstring>
#include <iomanip>
#include <sstream>
#include <string>
#include <utility>

namespace {

constexpr UINT_PTR kDecisionCaptureTimerId = 0x5243;
constexpr UINT_PTR kSelectionCaptureTimerId = 0x5244;
constexpr UINT_PTR kAccountRefreshTimerId = 0x5245;
constexpr UINT kSelectionCaptureIntervalMs = 500;
constexpr UINT kAccountRefreshIntervalMs = 1000;

struct ClassLocation {
    Il2CppClass* klass{};
    std::string image;
    std::string name_space;
};

struct MethodLocation {
    const MethodInfo* method{};
    std::uint32_t parameter_count{};
    std::string declaring_class;
};

using InstanceVoidMethod = void(__fastcall*)(void*, const MethodInfo*);
using InstanceIntVoidMethod = void(__fastcall*)(void*, std::int32_t, const MethodInfo*);
using InstancePointerVoidMethod = void(__fastcall*)(void*, void*, const MethodInfo*);
using InstanceTwoIntVoidMethod =
    void(__fastcall*)(void*, std::int32_t, std::int32_t, const MethodInfo*);
using InstancePointerReturnPointerMethod =
    void*(__fastcall*)(void*, void*, const MethodInfo*);
using InstanceIntGetter = std::int32_t(__fastcall*)(void*, const MethodInfo*);
using InstanceBoolGetter = bool(__fastcall*)(void*, const MethodInfo*);
using InstanceInt64Getter = std::int64_t(__fastcall*)(void*, const MethodInfo*);
using InstanceInt64IntVoidMethod = void(__fastcall*)(
    void*, std::int64_t, std::int32_t, const MethodInfo*);

constexpr std::uint32_t kCommandMagic = 0x5243484D;  // RCHM
constexpr std::uint32_t kCommandVersion = 4;
constexpr std::uint32_t kCommandFlagExecute = 1;

constexpr std::uint32_t kSharedStateMagic = 0x52434950;  // RCIP
constexpr std::uint32_t kSharedStateVersion = 3;
constexpr std::uint64_t kAgentBuildId = 2026082207ULL;
constexpr LONG kAgentStateInitializing = 1;
constexpr LONG kAgentStateReady = 2;
constexpr LONG kAgentStateFailed = 3;
constexpr LONG kAgentStateShuttingDown = 4;

template <std::size_t Capacity>
struct alignas(8) SharedJsonSlot {
    volatile LONG64 sequence{};
    std::uint32_t length{};
    std::uint32_t reserved{};
    char data[Capacity]{};
};

using AccountJsonSlot = SharedJsonSlot<4096>;
using DecisionJsonSlot = SharedJsonSlot<262144>;
using AckJsonSlot = SharedJsonSlot<4096>;
using LifecycleJsonSlot = SharedJsonSlot<16384>;
using BattleLedgerJsonSlot = SharedJsonSlot<65536>;
using RotationCatalogJsonSlot = SharedJsonSlot<262144>;
using DiagnosticJsonSlot = SharedJsonSlot<8192>;

struct alignas(8) AgentSharedState {
    std::uint32_t magic{};
    std::uint32_t shared_state_version{};
    std::uint32_t struct_size{};
    std::uint32_t pid{};
    std::uint64_t build_id{};
    std::uint64_t instance_id{};
    std::uint32_t command_version{};
    volatile LONG ready_state{};
    volatile LONG hooks_ready{};
    std::uint32_t reserved{};
    AccountJsonSlot account{};
    DecisionJsonSlot decision{};
    AckJsonSlot acknowledgement{};
    LifecycleJsonSlot lifecycle{};
    BattleLedgerJsonSlot battle_ledger{};
    RotationCatalogJsonSlot rotation_catalog{};
    DiagnosticJsonSlot diagnostic{};
};

static_assert(offsetof(AgentSharedState, account) == 48);
static_assert(sizeof(AccountJsonSlot) == 4112);
static_assert(sizeof(DecisionJsonSlot) == 262160);
static_assert(sizeof(AckJsonSlot) == 4112);
static_assert(sizeof(LifecycleJsonSlot) == 16400);
static_assert(sizeof(BattleLedgerJsonSlot) == 65552);
static_assert(sizeof(RotationCatalogJsonSlot) == 262160);
static_assert(sizeof(DiagnosticJsonSlot) == 8208);
static_assert(sizeof(AgentSharedState) == 622752);

constexpr std::uint32_t kControlMagic = 0x5243544C;  // RCTL
constexpr std::uint32_t kControlVersion = 1;
constexpr std::uint32_t kControlBeginTakeover = 1;
constexpr std::uint32_t kControlEndTakeover = 2;
constexpr std::uint32_t kControlFlagExpectedUser = 1U;
constexpr std::uint32_t kControlFlagBossModeExplicit = 2U;
constexpr std::uint32_t kControlFlagBossModeHydra = 4U;

constexpr LONG kTakeoverBossModeUnknown = 0;
constexpr LONG kTakeoverBossModeChimera = 1;
constexpr LONG kTakeoverBossModeHydra = 2;

struct TakeoverControlRequest {
    std::uint32_t magic{};
    std::uint32_t version{};
    std::uint64_t session_id{};
    std::uint32_t action{};
    std::uint32_t flags{};
    std::uint32_t nonce{};
    std::uint32_t reserved{};
};

static_assert(sizeof(TakeoverControlRequest) == 32);

constexpr std::uint32_t kLifecycleCommandMagic = 0x52434C43;  // RCLC
constexpr std::uint32_t kLifecycleCommandVersion = 2;
constexpr std::uint32_t kLifecycleStartBattle = 1;
constexpr std::uint32_t kLifecycleFreeRegroup = 2;
constexpr std::uint32_t kLifecyclePrepareFreeRegroup = 3;
constexpr std::uint32_t kLifecycleRefreshTeamSelection = 4;
constexpr std::uint32_t kLifecycleSelectHeroes = 5;
constexpr std::uint32_t kLifecycleRestartHydraResult = 6;

struct LifecycleCommandRequest {
    std::uint32_t magic{};
    std::uint32_t version{};
    std::uint64_t session_id{};
    std::uint64_t context{};
    std::uint32_t action{};
    std::uint32_t flags{};
    std::uint32_t nonce{};
    std::uint32_t reserved{};
    std::array<std::int32_t, 5> hero_ids{};
    std::uint32_t hero_count{};
};

static_assert(sizeof(LifecycleCommandRequest) == 64);

struct QueueCommandRequest {
    std::uint32_t magic{};
    std::uint32_t version{};
    std::uint64_t session_id{};
    std::uint64_t context{};
    std::uint64_t generator{};
    std::uint64_t mode{};
    std::uint64_t skill_data{};
    std::int32_t target_id{};
    std::int32_t skill_id{};
    std::int32_t verified_skill_type_id{};
    std::int32_t expected_area_id{INT_MIN};
    std::int32_t expected_region_id{INT_MIN};
    std::int32_t expected_round{INT_MIN};
    std::int32_t expected_turn{INT_MIN};
    std::int32_t expected_player_turn_count{INT_MIN};
    std::int32_t expected_active_hero_id{INT_MIN};
    std::int32_t expected_active_hero_turn_count{INT_MIN};
    std::int32_t expected_active_hero_form_index{INT_MIN};
    std::uint32_t flags{};
    std::uint32_t nonce{};
};

static_assert(sizeof(QueueCommandRequest) == 104);

Il2CppApi g_api{};
std::atomic<void*> g_battle_context{};
std::atomic<void*> g_command_generator{};
std::atomic<void*> g_battle_processor{};
std::atomic<bool> g_hooks_installed{};
InstanceVoidMethod g_original_on_enabled{};
InstanceVoidMethod g_original_on_disabled{};
InstanceVoidMethod g_original_request_command{};
InstanceIntVoidMethod g_original_select_skill{};
InstanceIntVoidMethod g_original_select_target{};
InstancePointerVoidMethod g_original_mode_select_skill{};
InstancePointerVoidMethod g_original_push_active_skill_data{};
InstanceVoidMethod g_original_battle_hud_pause_click{};
InstanceIntVoidMethod g_original_context_select_target{};
InstanceTwoIntVoidMethod g_original_create_manual_command{};
InstancePointerReturnPointerMethod g_original_get_acceptable_targets{};
const MethodInfo* g_create_manual_method{};
const MethodInfo* g_area_type_method{};
const MethodInfo* g_region_type_method{};
const MethodInfo* g_chimera_enabled_method{};
const MethodInfo* g_enemy_boss_current_method{};
const MethodInfo* g_acceptable_targets_method{};
InstanceIntGetter g_get_area_type{};
InstanceIntGetter g_get_region_type{};
InstanceBoolGetter g_get_chimera_enabled{};
InstanceBoolGetter g_get_enemy_boss_current{};
std::atomic<void*> g_localizer{};
const MethodInfo* g_localize_method{};
std::atomic<void*> g_static_data{};
std::array<std::string, 7> g_chimera_catalog_by_difficulty{};
std::string g_chimera_rotation_identity_json{};
std::string g_chimera_rotation_fingerprint{};
SRWLOCK g_chimera_catalog_lock = SRWLOCK_INIT;
Il2CppClass* g_alliance_chimera_difficulty_class{};
Il2CppClass* g_effect_kind_id_class{};
Il2CppClass* g_chimera_form_class{};
Il2CppClass* g_chimera_challenge_part_class{};
Il2CppClass* g_chimera_challenge_difficulty_class{};
Il2CppClass* g_status_effect_type_id_class{};
Il2CppClass* g_flexible_reward_type_class{};
Il2CppClass* g_resource_type_id_class{};
std::atomic<void*> g_battle_mode{};
std::atomic<void*> g_battle_hud_context{};
std::atomic<void*> g_selection_context{};
std::atomic<void*> g_result_context{};
std::array<void*, 32> g_active_skill_data{};
std::size_t g_active_skill_data_count{};
std::atomic<std::int32_t> g_skill_catalog_active_hero_id{INT_MIN};
std::atomic<std::int32_t> g_skill_catalog_active_hero_type_id{INT_MIN};
std::atomic<std::int32_t> g_skill_catalog_active_hero_turn_count{INT_MIN};
std::atomic<std::int32_t> g_skill_catalog_active_hero_form_index{INT_MIN};
std::atomic<std::int32_t> g_skill_catalog_active_hero_skills_update_counter{
    INT_MIN};
std::atomic<std::int64_t> g_last_chimera_damage{};
std::atomic<std::int64_t> g_last_chimera_competition_points{};
SRWLOCK g_active_skill_lock = SRWLOCK_INIT;
std::atomic<std::uint64_t> g_state_sequence{};

struct HydraDamageObservation {
    void* hero{};
    std::int32_t actor_id{INT_MIN};
    std::int64_t raw_damage{};
};

struct HydraUiDamageObservation {
    std::int32_t head_id{INT_MIN};
    std::int64_t damage{};
};

std::array<HydraDamageObservation, 96> g_hydra_damage_observations{};
std::int64_t g_hydra_accumulated_damage_raw{};
std::array<HydraUiDamageObservation, 512> g_hydra_ui_damage_observations{};
std::int64_t g_hydra_ui_total_damage{};
bool g_hydra_ui_damage_valid{};
void* g_hydra_damage_battle_context{};
SRWLOCK g_hydra_damage_lock = SRWLOCK_INIT;

void reset_hydra_damage_tracker(void* battle_context = nullptr) {
    AcquireSRWLockExclusive(&g_hydra_damage_lock);
    g_hydra_damage_observations.fill({});
    g_hydra_accumulated_damage_raw = 0;
    g_hydra_ui_damage_observations.fill({});
    g_hydra_ui_total_damage = 0;
    g_hydra_ui_damage_valid = false;
    g_hydra_damage_battle_context = battle_context;
    ReleaseSRWLockExclusive(&g_hydra_damage_lock);
}

void observe_hydra_ui_damage(std::int32_t head_id, std::int64_t damage) {
    if (head_id < 0 || damage < 0) {
        return;
    }
    AcquireSRWLockExclusive(&g_hydra_damage_lock);
    HydraUiDamageObservation* observation = nullptr;
    HydraUiDamageObservation* empty = nullptr;
    for (HydraUiDamageObservation& candidate :
         g_hydra_ui_damage_observations) {
        if (candidate.head_id == INT_MIN && !empty) {
            empty = &candidate;
        }
        if (candidate.head_id == head_id) {
            observation = &candidate;
            break;
        }
    }
    if (!observation) {
        observation = empty;
        if (observation) {
            observation->head_id = head_id;
        }
    }
    if (observation) {
        if (damage > observation->damage) {
            const std::int64_t delta = damage - observation->damage;
            if (g_hydra_ui_total_damage <= LLONG_MAX - delta) {
                g_hydra_ui_total_damage += delta;
            }
            observation->damage = damage;
        }
        g_hydra_ui_damage_valid = true;
    }
    ReleaseSRWLockExclusive(&g_hydra_damage_lock);
}

bool read_hydra_ui_damage(std::int64_t* total, std::size_t* head_count) {
    if (!total || !head_count) {
        return false;
    }
    AcquireSRWLockShared(&g_hydra_damage_lock);
    const bool valid = g_hydra_ui_damage_valid;
    *total = g_hydra_ui_total_damage;
    *head_count = 0;
    if (valid) {
        for (const HydraUiDamageObservation& observation :
             g_hydra_ui_damage_observations) {
            if (observation.head_id != INT_MIN) {
                ++*head_count;
            }
        }
    }
    ReleaseSRWLockShared(&g_hydra_damage_lock);
    return valid;
}

std::int64_t observe_hydra_damage(void* battle_context,
                                  std::int32_t actor_id, void* hero,
                                  std::int64_t raw_damage) {
    if (!battle_context || !hero || actor_id < 0 || raw_damage < 0) {
        return -1;
    }
    AcquireSRWLockExclusive(&g_hydra_damage_lock);
    if (g_hydra_damage_battle_context != battle_context) {
        g_hydra_damage_observations.fill({});
        g_hydra_accumulated_damage_raw = 0;
        g_hydra_damage_battle_context = battle_context;
    }

    HydraDamageObservation* observation = nullptr;
    HydraDamageObservation* empty = nullptr;
    for (HydraDamageObservation& candidate : g_hydra_damage_observations) {
        if (!candidate.hero && !empty) {
            empty = &candidate;
        }
        if (candidate.hero == hero && candidate.actor_id == actor_id) {
            observation = &candidate;
            break;
        }
    }
    if (!observation) {
        observation = empty;
        if (observation) {
            observation->hero = hero;
            observation->actor_id = actor_id;
            observation->raw_damage = 0;
        }
    }
    if (observation) {
        const std::int64_t delta = raw_damage >= observation->raw_damage
            ? raw_damage - observation->raw_damage
            : raw_damage;
        if (delta > 0 &&
            g_hydra_accumulated_damage_raw <= LLONG_MAX - delta) {
            g_hydra_accumulated_damage_raw += delta;
        }
        observation->raw_damage = raw_damage;
    }
    const std::int64_t total = g_hydra_accumulated_damage_raw;
    ReleaseSRWLockExclusive(&g_hydra_damage_lock);
    return total;
}

HWND g_game_window{};
WNDPROC g_original_window_proc{};
UINT g_command_message{};
DWORD g_window_thread_id{};
std::atomic<bool> g_window_dispatch_installed{};
std::atomic<bool> g_command_pending{};
QueueCommandRequest g_pending_command{};
SRWLOCK g_command_lock = SRWLOCK_INIT;
std::atomic<bool> g_lifecycle_command_pending{};
LifecycleCommandRequest g_pending_lifecycle_command{};
SRWLOCK g_lifecycle_command_lock = SRWLOCK_INIT;
HANDLE g_shared_state_mapping{};
AgentSharedState* g_shared_state{};
std::atomic<std::uint64_t> g_takeover_session{};
std::atomic<std::uint64_t> g_takeover_user_id{};
std::atomic<LONG> g_takeover_state{};
std::atomic<LONG> g_takeover_boss_mode{};
std::atomic<std::uint64_t> g_takeover_sequence{};
SRWLOCK g_takeover_lock = SRWLOCK_INIT;
std::atomic<LONG> g_internal_action_depth{};
std::atomic<void*> g_app_model_instance{};
const MethodInfo* g_app_model_instance_method{};
const MethodInfo* g_app_model_read_user_method{};
const MethodInfo* g_user_read_guard_dispose_method{};

bool validate_takeover_account();
bool object_has_class_name(void* object, const char* expected);
void* refresh_app_model_instance();
void log_account_identity(void* app_model_instance);

constexpr LONG kScreenUnknown = 0;
constexpr LONG kScreenTeamSelection = 1;
constexpr LONG kScreenBattle = 2;
constexpr LONG kScreenResult = 3;

struct SelectionSnapshot {
    bool valid{};
    bool filled{};
    bool auto_battle{};
    bool quick_battle{};
    bool hydra{};
    std::int32_t area_id{INT_MIN};
    std::int32_t stage_id{INT_MIN};
    std::array<std::int32_t, 6> hero_ids{};
    std::array<std::int32_t, 6> hero_type_ids{};
    std::size_t hero_count{};
};

SelectionSnapshot g_selection_snapshot{};
SelectionSnapshot g_last_started_selection{};
SRWLOCK g_ui_state_lock = SRWLOCK_INIT;
std::atomic<LONG> g_screen_state{kScreenUnknown};
std::atomic<void*> g_selection_user{};

InstanceVoidMethod g_original_selection_on_enabled{};
InstanceVoidMethod g_original_selection_on_disabled{};
InstancePointerVoidMethod g_original_selection_refresh{};
InstanceVoidMethod g_original_selection_start_battle_click{};
InstanceVoidMethod g_original_hydra_selection_on_enabled{};
InstanceVoidMethod g_original_hydra_selection_on_disabled{};
InstancePointerVoidMethod g_original_hydra_selection_refresh{};
InstanceVoidMethod g_original_hydra_selection_start_battle_click{};
InstanceInt64Getter g_original_result_total_damage{};
InstanceInt64Getter g_original_hydra_result_total_damage{};
InstanceInt64IntVoidMethod g_original_hydra_damage_counter_change{};
std::atomic<bool> g_hydra_damage_counter_hook_installed{};
const MethodInfo* g_selection_filled_method{};
const MethodInfo* g_selection_heroes_method{};
const MethodInfo* g_selection_hero_method{};
const MethodInfo* g_selection_area_method{};
const MethodInfo* g_selection_stage_method{};
const MethodInfo* g_selection_start_battle_click_method{};
const MethodInfo* g_selection_hero_picked_method{};
const MethodInfo* g_auto_battle_selected_method{};
const MethodInfo* g_quick_battle_active_method{};
const MethodInfo* g_result_completed_challenges_method{};
const MethodInfo* g_hydra_selection_filled_method{};
const MethodInfo* g_hydra_selection_heroes_method{};
const MethodInfo* g_hydra_selection_hero_method{};
const MethodInfo* g_hydra_selection_area_method{};
const MethodInfo* g_hydra_selection_stage_method{};
const MethodInfo* g_hydra_quick_battle_active_method{};
const MethodInfo* g_hydra_selection_start_battle_click_method{};
const MethodInfo* g_execute_cancel_chimera_method{};
const MethodInfo* g_cancel_chimera_method{};
const MethodInfo* g_hydra_total_damage_method{};
const MethodInfo* g_hydra_result_restart_pressed_method{};

struct ExecutedTurnToken {
    std::uint64_t mode{};
    std::int32_t round{};
    std::int32_t turn{};
    std::int32_t player_turn_count{};
    std::int32_t active_hero_id{};
    std::int32_t active_hero_turn_count{};
    std::int32_t active_hero_form_index{};
};

ExecutedTurnToken g_executed_turn{};
bool g_executed_turn_valid{};
SRWLOCK g_executed_turn_lock = SRWLOCK_INIT;

std::string json_escape(const std::string& value);
void diagnostic(const std::string& message);
void capture_decision_state(void* generator);
void capture_selection_state(void* self, const char* reason,
                             void* selection_user = nullptr,
                             bool publish_unchanged = true);
void append_static_hero_catalog(
    std::ostringstream& output,
    const std::array<std::int32_t, 6>& hero_ids,
    std::size_t hero_count);
void append_static_hydra_head_catalog(std::ostringstream& output);
bool refresh_chimera_rotation_catalog(bool force);

template <std::size_t Capacity>
void publish_shared_json(SharedJsonSlot<Capacity>& slot,
                         const std::string& payload) {
    const std::size_t copy_length =
        payload.size() < Capacity ? payload.size() : Capacity - 1;
    InterlockedIncrement64(&slot.sequence);
    MemoryBarrier();
    std::memcpy(slot.data, payload.data(), copy_length);
    slot.data[copy_length] = '\0';
    slot.length = static_cast<std::uint32_t>(copy_length);
    MemoryBarrier();
    InterlockedIncrement64(&slot.sequence);
}

bool initialize_shared_state() {
    std::wostringstream name;
    name << L"Local\\RaidChimeraAgentState-" << GetCurrentProcessId();
    g_shared_state_mapping = CreateFileMappingW(
        INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
        static_cast<DWORD>(sizeof(AgentSharedState)), name.str().c_str());
    if (!g_shared_state_mapping) {
        return false;
    }
    g_shared_state = static_cast<AgentSharedState*>(MapViewOfFile(
        g_shared_state_mapping, FILE_MAP_ALL_ACCESS, 0, 0,
        sizeof(AgentSharedState)));
    if (!g_shared_state) {
        CloseHandle(g_shared_state_mapping);
        g_shared_state_mapping = nullptr;
        return false;
    }
    ZeroMemory(g_shared_state, sizeof(AgentSharedState));
    g_shared_state->magic = kSharedStateMagic;
    g_shared_state->shared_state_version = kSharedStateVersion;
    g_shared_state->struct_size = sizeof(AgentSharedState);
    g_shared_state->pid = GetCurrentProcessId();
    g_shared_state->build_id = kAgentBuildId;
    g_shared_state->instance_id =
        (static_cast<std::uint64_t>(GetTickCount64()) << 16) ^
        reinterpret_cast<std::uintptr_t>(g_shared_state);
    g_shared_state->command_version = kCommandVersion;
    InterlockedExchange(&g_shared_state->ready_state,
                        kAgentStateInitializing);
    return true;
}

void set_agent_state(LONG state, bool hooks_ready = false) {
    if (!g_shared_state) {
        return;
    }
    InterlockedExchange(&g_shared_state->hooks_ready, hooks_ready ? 1 : 0);
    InterlockedExchange(&g_shared_state->ready_state, state);
}

void publish_ack(const QueueCommandRequest& request, const char* status,
                 const char* reason = nullptr) {
    if (!g_shared_state) {
        return;
    }
    std::ostringstream output;
    output << "{\"type\":\"command_ack\",\"sessionId\":"
           << request.session_id << ",\"nonce\":" << request.nonce
           << ",\"status\":\"" << status << "\"";
    if (reason && *reason) {
        output << ",\"reason\":\"" << json_escape(reason) << "\"";
    }
    output << ",\"observedAtTick\":" << GetTickCount64() << '}';
    publish_shared_json(g_shared_state->acknowledgement, output.str());
}

const char* takeover_state_name(LONG state) {
    switch (state) {
    case 1:
        return "active";
    case 2:
        return "interrupted";
    default:
        return "idle";
    }
}

const char* screen_state_name(LONG state) {
    switch (state) {
    case kScreenTeamSelection:
        return "team_selection";
    case kScreenBattle:
        return "battle";
    case kScreenResult:
        return "result";
    default:
        return "unknown";
    }
}

void publish_takeover_state_locked(const char* reason,
                                   const char* input_source = nullptr) {
    if (!g_shared_state) {
        return;
    }
    const std::uint64_t sequence =
        g_takeover_sequence.fetch_add(1, std::memory_order_acq_rel) + 1;
    const LONG state = g_takeover_state.load(std::memory_order_acquire);
    std::ostringstream output;
    output << "{\"type\":\"lifecycle_state\",\"sequence\":" << sequence
           << ",\"pid\":" << GetCurrentProcessId()
           << ",\"takeoverState\":\"" << takeover_state_name(state)
           << "\",\"sessionId\":"
           << g_takeover_session.load(std::memory_order_acquire)
           << ",\"reason\":\"" << json_escape(reason ? reason : "")
           << "\",\"screen\":\""
           << screen_state_name(g_screen_state.load(std::memory_order_acquire))
           << "\",\"observedAtTick\":" << GetTickCount64();
    if (g_screen_state.load(std::memory_order_acquire) ==
        kScreenTeamSelection) {
        SelectionSnapshot selection{};
        AcquireSRWLockShared(&g_ui_state_lock);
        selection = g_selection_snapshot;
        ReleaseSRWLockShared(&g_ui_state_lock);
        output << ",\"selection\":{\"valid\":"
               << (selection.valid ? "true" : "false")
               << ",\"filled\":" << (selection.filled ? "true" : "false")
               << ",\"autoBattle\":"
               << (selection.auto_battle ? "true" : "false")
               << ",\"quickBattle\":"
               << (selection.quick_battle ? "true" : "false")
               << ",\"bossMode\":\""
               << (selection.hydra ? "hydra" : "chimera") << "\""
               << ",\"areaTypeId\":" << selection.area_id
               << ",\"stageId\":" << selection.stage_id
               << ",\"context\":"
               << reinterpret_cast<std::uintptr_t>(
                      g_selection_context.load(std::memory_order_acquire))
               << ",\"heroIds\":[";
        for (std::size_t index = 0; index < selection.hero_count; ++index) {
            if (index) {
                output << ',';
            }
            output << selection.hero_ids[index];
        }
        output << "],\"heroTypeIds\":[";
        for (std::size_t index = 0; index < selection.hero_count; ++index) {
            if (index) {
                output << ',';
            }
            output << selection.hero_type_ids[index];
        }
        output << "],\"canStart\":"
               << (selection.valid &&
                           g_selection_context.load(std::memory_order_acquire) &&
                           !selection.quick_battle
                       ? "true"
                       : "false")
               << '}';
    }
    else if (g_screen_state.load(std::memory_order_acquire) == kScreenBattle) {
        SelectionSnapshot started_selection{};
        AcquireSRWLockShared(&g_ui_state_lock);
        started_selection = g_last_started_selection;
        ReleaseSRWLockShared(&g_ui_state_lock);
        const LONG takeover_boss_mode =
            g_takeover_boss_mode.load(std::memory_order_acquire);
        const bool battle_is_hydra = started_selection.valid
            ? started_selection.hydra
            : takeover_boss_mode == kTakeoverBossModeHydra;
        output << ",\"battle\":{\"context\":"
               << reinterpret_cast<std::uintptr_t>(
                      g_battle_context.load(std::memory_order_acquire))
               << ",\"bossMode\":\""
               << (battle_is_hydra ? "hydra" : "chimera")
               << "\"";
        if (started_selection.valid && started_selection.stage_id > 0) {
            output << ",\"stageId\":" << started_selection.stage_id
                   << ",\"heroIds\":[";
            for (std::size_t index = 0;
                 index < started_selection.hero_count; ++index) {
                if (index) {
                    output << ',';
                }
                output << started_selection.hero_ids[index];
            }
            output << "],\"heroTypeIds\":[";
            for (std::size_t index = 0;
                 index < started_selection.hero_count; ++index) {
                if (index) {
                    output << ',';
                }
                output << started_selection.hero_type_ids[index];
            }
            output << ']';
        }
        output << '}';
    }
    else if (g_screen_state.load(std::memory_order_acquire) == kScreenResult) {
        void* result_context =
            g_result_context.load(std::memory_order_acquire);
        const bool result_is_hydra = result_context &&
            object_has_class_name(
                result_context, "BattleFinishAllianceHydraDialogContext");
        output << ",\"result\":{\"context\":"
               << reinterpret_cast<std::uintptr_t>(result_context)
               << ",\"bossMode\":\""
               << (result_is_hydra ? "hydra" : "chimera") << "\"}";
    }
    if (input_source && *input_source) {
        output << ",\"inputSource\":\"" << json_escape(input_source)
               << "\"";
    }
    output << '}';
    publish_shared_json(g_shared_state->lifecycle, output.str());
}

void begin_takeover(std::uint64_t session_id, std::uint64_t user_id,
                    LONG boss_mode) {
    AcquireSRWLockExclusive(&g_takeover_lock);
    g_command_pending.store(false, std::memory_order_release);
    g_lifecycle_command_pending.store(false, std::memory_order_release);
    g_takeover_session.store(session_id, std::memory_order_release);
    g_takeover_user_id.store(user_id, std::memory_order_release);
    g_takeover_boss_mode.store(boss_mode, std::memory_order_release);
    g_takeover_state.store(1, std::memory_order_release);
    publish_takeover_state_locked("controller_armed");
    ReleaseSRWLockExclusive(&g_takeover_lock);
}

void end_takeover(std::uint64_t session_id, const char* reason) {
    AcquireSRWLockExclusive(&g_takeover_lock);
    const std::uint64_t active_session =
        g_takeover_session.load(std::memory_order_acquire);
    if (!session_id || active_session == session_id) {
        g_command_pending.store(false, std::memory_order_release);
        g_lifecycle_command_pending.store(false, std::memory_order_release);
        g_takeover_state.store(0, std::memory_order_release);
        g_takeover_session.store(0, std::memory_order_release);
        g_takeover_user_id.store(0, std::memory_order_release);
        g_takeover_boss_mode.store(kTakeoverBossModeUnknown,
                                   std::memory_order_release);
        publish_takeover_state_locked(reason ? reason : "controller_disarmed");
    }
    ReleaseSRWLockExclusive(&g_takeover_lock);
}

void interrupt_takeover(const char* reason, const char* input_source) {
    AcquireSRWLockExclusive(&g_takeover_lock);
    if (g_takeover_state.load(std::memory_order_acquire) == 1) {
        g_command_pending.store(false, std::memory_order_release);
        g_lifecycle_command_pending.store(false, std::memory_order_release);
        g_takeover_state.store(2, std::memory_order_release);
        publish_takeover_state_locked(reason, input_source);
        diagnostic(std::string("takeover_interrupted reason=") + reason +
                   " source=" + (input_source ? input_source : "unknown"));
    }
    ReleaseSRWLockExclusive(&g_takeover_lock);
}

void publish_lifecycle(const char* reason) {
    AcquireSRWLockExclusive(&g_takeover_lock);
    publish_takeover_state_locked(reason ? reason : "state_changed");
    ReleaseSRWLockExclusive(&g_takeover_lock);
}

void publish_lifecycle_ack(const LifecycleCommandRequest& request,
                           const char* status, const char* reason = nullptr) {
    if (!g_shared_state) {
        return;
    }
    std::ostringstream output;
    output << "{\"type\":\"lifecycle_ack\",\"sessionId\":"
           << request.session_id << ",\"nonce\":" << request.nonce
           << ",\"action\":" << request.action << ",\"status\":\""
           << status << "\"";
    if (reason && *reason) {
        output << ",\"reason\":\"" << json_escape(reason) << "\"";
    }
    output << ",\"observedAtTick\":" << GetTickCount64() << '}';
    publish_shared_json(g_shared_state->acknowledgement, output.str());
}

void diagnostic(const std::string& message) {
    OutputDebugStringA((message + "\n").c_str());
    if (g_shared_state) {
        publish_shared_json(g_shared_state->diagnostic, message);
    }
}

template <typename T>
bool safe_read(void* object, std::size_t offset, T& value) {
    if (!object) {
        return false;
    }
    __try {
        value = *reinterpret_cast<T*>(static_cast<unsigned char*>(object) + offset);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool safe_static_field_value(FieldInfo* field, void*& value) {
    if (!field || !g_api.field_static_get_value) {
        return false;
    }
    __try {
        g_api.field_static_get_value(field, &value);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        value = nullptr;
        return false;
    }
}

bool safe_static_field_int32(FieldInfo* field, std::int32_t& value) {
    if (!field || !g_api.field_static_get_value) {
        return false;
    }
    __try {
        g_api.field_static_get_value(field, &value);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        value = 0;
        return false;
    }
}

std::string enum_member_name(Il2CppClass* klass, std::int32_t wanted) {
    if (!klass || !g_api.class_get_fields || !g_api.field_get_name ||
        !g_api.field_get_flags) {
        return {};
    }
    constexpr std::uint32_t kFieldAttributeLiteral = 0x0040;
    void* iterator = nullptr;
    while (FieldInfo* field = g_api.class_get_fields(klass, &iterator)) {
        const std::uint32_t flags = g_api.field_get_flags(field);
        if (!(flags & kFieldAttributeLiteral)) {
            continue;
        }
        std::int32_t value = 0;
        const char* name = g_api.field_get_name(field);
        if (name && safe_static_field_int32(field, value) && value == wanted) {
            return name;
        }
    }
    return {};
}

std::string enum_catalog_json(Il2CppClass* klass) {
    std::ostringstream output;
    output << '[';
    if (!klass || !g_api.class_get_fields || !g_api.field_get_name ||
        !g_api.field_get_flags) {
        output << ']';
        return output.str();
    }
    constexpr std::uint32_t kFieldAttributeLiteral = 0x0040;
    bool first = true;
    void* iterator = nullptr;
    while (FieldInfo* field = g_api.class_get_fields(klass, &iterator)) {
        const std::uint32_t flags = g_api.field_get_flags(field);
        if (!(flags & kFieldAttributeLiteral)) {
            continue;
        }
        std::int32_t value = 0;
        const char* name = g_api.field_get_name(field);
        if (!name || !*name || !safe_static_field_int32(field, value) ||
            value <= 0) {
            continue;
        }
        if (!first) {
            output << ',';
        }
        first = false;
        output << "{\"id\":" << value << ",\"name\":\""
               << json_escape(name) << "\"}";
    }
    output << ']';
    return output.str();
}

bool safe_il2cpp_string_view(Il2CppString* value, const wchar_t** characters,
                             std::int32_t* length) {
    if (!value || !g_api.string_length || !g_api.string_chars) {
        return false;
    }
    __try {
        *length = g_api.string_length(value);
        *characters = g_api.string_chars(value);
        return *length >= 0 && *length <= 16384 && *characters;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

std::string il2cpp_string_utf8(void* value) {
    const wchar_t* characters = nullptr;
    std::int32_t length = 0;
    if (!safe_il2cpp_string_view(reinterpret_cast<Il2CppString*>(value),
                                 &characters, &length) || !length) {
        return {};
    }
    const int utf8_length = WideCharToMultiByte(
        CP_UTF8, 0, characters, length, nullptr, 0, nullptr, nullptr);
    if (utf8_length <= 0) {
        return {};
    }
    std::string result(static_cast<std::size_t>(utf8_length), '\0');
    WideCharToMultiByte(CP_UTF8, 0, characters, length, result.data(),
                        utf8_length, nullptr, nullptr);
    return result;
}

bool find_field_offset(Il2CppClass* klass, const char* primary,
                       const char* fallback, std::size_t& offset) {
    for (int depth = 0; klass && depth < 8; ++depth) {
        void* iterator = nullptr;
        while (FieldInfo* field = g_api.class_get_fields(klass, &iterator)) {
            const char* name = g_api.field_get_name(field);
            if (name && (std::strcmp(name, primary) == 0 ||
                         (fallback && std::strcmp(name, fallback) == 0))) {
                offset = g_api.field_get_offset(field);
                return true;
            }
        }
        klass = g_api.class_get_parent(klass);
    }
    return false;
}

template <typename T>
bool safe_read_field(void* object, const char* primary, const char* fallback,
                     T& value) {
    Il2CppClass* klass = nullptr;
    std::size_t offset = 0;
    return safe_read(object, 0, klass) && klass &&
           find_field_offset(klass, primary, fallback, offset) &&
           safe_read(object, offset, value);
}

bool safe_invoke_localize(void* localizer, const MethodInfo* method,
                          void* key, std::int32_t priority, void** result) {
    if (!localizer || !method || !key || !result) {
        return false;
    }
    __try {
        void* parameters[2] = {key, &priority};
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, localizer, parameters, &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

struct ResolvedText {
    std::string key;
    std::string default_value;
    std::string display;
};

ResolvedText resolve_shared_text(void* shared_text_key) {
    ResolvedText resolved{};
    Il2CppClass* klass = nullptr;
    if (!safe_read(shared_text_key, 0, klass) || !klass) {
        return resolved;
    }
    std::size_t key_offset = 0;
    std::size_t default_offset = 0;
    void* key = nullptr;
    void* default_value = nullptr;
    if (!find_field_offset(klass, "Key", nullptr, key_offset) ||
        !find_field_offset(klass, "DefaultValue", nullptr, default_offset) ||
        !safe_read(shared_text_key, key_offset, key)) {
        return resolved;
    }
    safe_read(shared_text_key, default_offset, default_value);
    resolved.key = il2cpp_string_utf8(key);
    resolved.default_value = il2cpp_string_utf8(default_value);

    void* localizer = g_localizer.load(std::memory_order_acquire);
    for (std::int32_t priority = 0;
         localizer && g_localize_method && priority < 2; ++priority) {
        void* result = nullptr;
        if (!safe_invoke_localize(localizer, g_localize_method, key, priority,
                                  &result)) {
            continue;
        }
        const std::string candidate = il2cpp_string_utf8(result);
        if (!candidate.empty() && candidate != resolved.key) {
            resolved.display = candidate;
            break;
        }
    }
    if (resolved.display.empty()) {
        resolved.display = !resolved.default_value.empty()
            ? resolved.default_value
            : resolved.key;
    }
    return resolved;
}

bool safe_class_value_size(Il2CppClass* klass, std::int32_t* size) {
    __try {
        std::uint32_t alignment = 0;
        *size = g_api.class_value_size(klass, &alignment);
        return *size > 0 && *size <= 256;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

struct IntDictionaryKeys {
    std::array<std::int32_t, 64> values{};
    std::size_t count{};
    std::int32_t reported_count{};
    std::int32_t stage{};
    std::int32_t entry_size{};
    std::size_t entries_offset{};
    std::size_t count_offset{};
    std::size_t array_length{};
    std::size_t key_offset{};
    std::size_t hash_offset{};
    bool valid{};
};

IntDictionaryKeys read_int_dictionary_keys(void* dictionary) {
    IntDictionaryKeys snapshot{};
    Il2CppClass* dictionary_class = nullptr;
    if (!safe_read(dictionary, 0, dictionary_class) || !dictionary_class) {
        snapshot.stage = 1;
        return snapshot;
    }

    std::size_t entries_offset = 0;
    std::size_t count_offset = 0;
    if (!find_field_offset(dictionary_class, "_entries", "entries",
                           entries_offset) ||
        !find_field_offset(dictionary_class, "_count", "count", count_offset)) {
        snapshot.stage = 2;
        return snapshot;
    }
    snapshot.entries_offset = entries_offset;
    snapshot.count_offset = count_offset;

    void* entries = nullptr;
    if (!safe_read(dictionary, entries_offset, entries) || !entries ||
        !safe_read(dictionary, count_offset, snapshot.reported_count) ||
        snapshot.reported_count < 0 || snapshot.reported_count > 4096) {
        snapshot.stage = 3;
        return snapshot;
    }

    Il2CppClass* array_class = nullptr;
    std::size_t array_length = 0;
    if (!safe_read(entries, 0, array_class) || !array_class ||
        !safe_read(entries, 24, array_length) || array_length > 4096) {
        snapshot.stage = 4;
        return snapshot;
    }
    snapshot.array_length = array_length;
    Il2CppClass* entry_class = g_api.class_get_element_class(array_class);
    std::int32_t entry_size = 0;
    std::size_t key_offset = 0;
    std::size_t hash_offset = 0;
    if (!entry_class || !safe_class_value_size(entry_class, &entry_size) ||
        !find_field_offset(entry_class, "key", nullptr, key_offset)) {
        snapshot.stage = 5;
        return snapshot;
    }
    snapshot.entry_size = entry_size;
    snapshot.key_offset = key_offset;
    const bool has_hash =
        find_field_offset(entry_class, "hashCode", "_hashCode", hash_offset);
    constexpr std::size_t kValueTypeObjectHeader = 16;
    if (key_offset >= kValueTypeObjectHeader) {
        key_offset -= kValueTypeObjectHeader;
    }
    if (has_hash && hash_offset >= kValueTypeObjectHeader) {
        hash_offset -= kValueTypeObjectHeader;
    }
    snapshot.key_offset = key_offset;
    snapshot.hash_offset = hash_offset;
    if (key_offset + sizeof(std::int32_t) >
            static_cast<std::size_t>(entry_size) ||
        (has_hash && hash_offset + sizeof(std::int32_t) >
                         static_cast<std::size_t>(entry_size))) {
        snapshot.stage = 6;
        return snapshot;
    }

    const std::size_t used = (std::min)(
        static_cast<std::size_t>(snapshot.reported_count), array_length);
    auto* vector = static_cast<unsigned char*>(entries) + 32;
    for (std::size_t index = 0; index < used && snapshot.count < snapshot.values.size();
         ++index) {
        void* entry = vector + index * static_cast<std::size_t>(entry_size);
        std::int32_t hash_code = 0;
        std::int32_t key = 0;
        if ((has_hash && (!safe_read(entry, hash_offset, hash_code) ||
                          hash_code < 0)) ||
            !safe_read(entry, key_offset, key) || key < 0) {
            continue;
        }
        bool duplicate = false;
        for (std::size_t existing = 0; existing < snapshot.count; ++existing) {
            duplicate = duplicate || snapshot.values[existing] == key;
        }
        if (!duplicate) {
            snapshot.values[snapshot.count++] = key;
        }
    }
    snapshot.valid = true;
    snapshot.stage = 7;
    return snapshot;
}

struct IntObjectDictionaryItems {
    std::array<std::int32_t, 64> keys{};
    std::array<void*, 64> objects{};
    std::size_t count{};
    bool valid{};
};

IntObjectDictionaryItems read_int_object_dictionary(void* dictionary) {
    IntObjectDictionaryItems snapshot{};
    Il2CppClass* dictionary_class = nullptr;
    if (!safe_read(dictionary, 0, dictionary_class) || !dictionary_class) {
        return snapshot;
    }
    std::size_t entries_offset = 0;
    std::size_t count_offset = 0;
    if (!find_field_offset(dictionary_class, "_entries", "entries",
                           entries_offset) ||
        !find_field_offset(dictionary_class, "_count", "count", count_offset)) {
        return snapshot;
    }
    void* entries = nullptr;
    std::int32_t reported_count = 0;
    if (!safe_read(dictionary, entries_offset, entries) || !entries ||
        !safe_read(dictionary, count_offset, reported_count) ||
        reported_count < 0 || reported_count > 4096) {
        return snapshot;
    }
    Il2CppClass* array_class = nullptr;
    std::size_t array_length = 0;
    if (!safe_read(entries, 0, array_class) || !array_class ||
        !safe_read(entries, 24, array_length) || array_length > 4096) {
        return snapshot;
    }
    Il2CppClass* entry_class = g_api.class_get_element_class(array_class);
    std::int32_t entry_size = 0;
    std::size_t key_offset = 0;
    std::size_t value_offset = 0;
    std::size_t hash_offset = 0;
    if (!entry_class || !safe_class_value_size(entry_class, &entry_size) ||
        !find_field_offset(entry_class, "key", nullptr, key_offset) ||
        !find_field_offset(entry_class, "value", nullptr, value_offset)) {
        return snapshot;
    }
    const bool has_hash =
        find_field_offset(entry_class, "hashCode", "_hashCode", hash_offset);
    constexpr std::size_t kValueTypeObjectHeader = 16;
    if (key_offset >= kValueTypeObjectHeader) {
        key_offset -= kValueTypeObjectHeader;
    }
    if (value_offset >= kValueTypeObjectHeader) {
        value_offset -= kValueTypeObjectHeader;
    }
    if (has_hash && hash_offset >= kValueTypeObjectHeader) {
        hash_offset -= kValueTypeObjectHeader;
    }
    if (key_offset + sizeof(std::int32_t) >
            static_cast<std::size_t>(entry_size) ||
        value_offset + sizeof(void*) > static_cast<std::size_t>(entry_size) ||
        (has_hash && hash_offset + sizeof(std::int32_t) >
                         static_cast<std::size_t>(entry_size))) {
        return snapshot;
    }

    const std::size_t used = (std::min)(
        static_cast<std::size_t>(reported_count), array_length);
    auto* vector = static_cast<unsigned char*>(entries) + 32;
    for (std::size_t index = 0;
         index < used && snapshot.count < snapshot.keys.size(); ++index) {
        void* entry = vector + index * static_cast<std::size_t>(entry_size);
        std::int32_t hash_code = 0;
        std::int32_t key = 0;
        void* value = nullptr;
        if ((has_hash && (!safe_read(entry, hash_offset, hash_code) ||
                          hash_code < 0)) ||
            !safe_read(entry, key_offset, key) || key < 0 ||
            !safe_read(entry, value_offset, value) || !value) {
            continue;
        }
        snapshot.keys[snapshot.count] = key;
        snapshot.objects[snapshot.count] = value;
        ++snapshot.count;
    }
    snapshot.valid = true;
    return snapshot;
}

struct IntIntDictionaryItems {
    std::array<std::int32_t, 64> keys{};
    std::array<std::int32_t, 64> values{};
    std::size_t count{};
    bool valid{};
};

IntIntDictionaryItems read_int_int_dictionary(void* dictionary) {
    IntIntDictionaryItems snapshot{};
    Il2CppClass* dictionary_class = nullptr;
    if (!safe_read(dictionary, 0, dictionary_class) || !dictionary_class) {
        return snapshot;
    }
    std::size_t entries_offset = 0;
    std::size_t count_offset = 0;
    if (!find_field_offset(dictionary_class, "_entries", "entries",
                           entries_offset) ||
        !find_field_offset(dictionary_class, "_count", "count",
                           count_offset)) {
        return snapshot;
    }
    void* entries = nullptr;
    std::int32_t reported_count = 0;
    if (!safe_read(dictionary, entries_offset, entries) || !entries ||
        !safe_read(dictionary, count_offset, reported_count) ||
        reported_count < 0 || reported_count > 4096) {
        return snapshot;
    }
    Il2CppClass* array_class = nullptr;
    std::size_t array_length = 0;
    if (!safe_read(entries, 0, array_class) || !array_class ||
        !safe_read(entries, 24, array_length) || array_length > 4096) {
        return snapshot;
    }
    Il2CppClass* entry_class = g_api.class_get_element_class(array_class);
    std::int32_t entry_size = 0;
    std::size_t key_offset = 0;
    std::size_t value_offset = 0;
    std::size_t hash_offset = 0;
    if (!entry_class || !safe_class_value_size(entry_class, &entry_size) ||
        !find_field_offset(entry_class, "key", nullptr, key_offset) ||
        !find_field_offset(entry_class, "value", nullptr, value_offset)) {
        return snapshot;
    }
    const bool has_hash =
        find_field_offset(entry_class, "hashCode", "_hashCode", hash_offset);
    constexpr std::size_t kValueTypeObjectHeader = 16;
    if (key_offset >= kValueTypeObjectHeader) {
        key_offset -= kValueTypeObjectHeader;
    }
    if (value_offset >= kValueTypeObjectHeader) {
        value_offset -= kValueTypeObjectHeader;
    }
    if (has_hash && hash_offset >= kValueTypeObjectHeader) {
        hash_offset -= kValueTypeObjectHeader;
    }
    if (key_offset + sizeof(std::int32_t) >
            static_cast<std::size_t>(entry_size) ||
        value_offset + sizeof(std::int32_t) >
            static_cast<std::size_t>(entry_size) ||
        (has_hash && hash_offset + sizeof(std::int32_t) >
                         static_cast<std::size_t>(entry_size))) {
        return snapshot;
    }
    const std::size_t used = (std::min)(
        static_cast<std::size_t>(reported_count), array_length);
    auto* vector = static_cast<unsigned char*>(entries) + 32;
    for (std::size_t index = 0;
         index < used && snapshot.count < snapshot.keys.size(); ++index) {
        void* entry = vector + index * static_cast<std::size_t>(entry_size);
        std::int32_t hash_code = 0;
        std::int32_t key = 0;
        std::int32_t value = 0;
        if ((has_hash && (!safe_read(entry, hash_offset, hash_code) ||
                          hash_code < 0)) ||
            !safe_read(entry, key_offset, key) || key < 0 ||
            !safe_read(entry, value_offset, value)) {
            continue;
        }
        snapshot.keys[snapshot.count] = key;
        snapshot.values[snapshot.count] = value;
        ++snapshot.count;
    }
    snapshot.valid = true;
    return snapshot;
}

struct ObjectListItems {
    std::array<void*, 128> objects{};
    std::size_t count{};
    bool valid{};
};

ObjectListItems read_object_dictionary_values(void* dictionary) {
    ObjectListItems snapshot{};
    Il2CppClass* dictionary_class = nullptr;
    if (!safe_read(dictionary, 0, dictionary_class) || !dictionary_class) {
        return snapshot;
    }
    std::size_t entries_offset = 0;
    std::size_t count_offset = 0;
    if (!find_field_offset(dictionary_class, "_entries", "entries",
                           entries_offset) ||
        !find_field_offset(dictionary_class, "_count", "count",
                           count_offset)) {
        return snapshot;
    }
    void* entries = nullptr;
    std::int32_t reported_count = 0;
    if (!safe_read(dictionary, entries_offset, entries) || !entries ||
        !safe_read(dictionary, count_offset, reported_count) ||
        reported_count < 0 || reported_count > 4096) {
        return snapshot;
    }
    Il2CppClass* array_class = nullptr;
    std::size_t array_length = 0;
    if (!safe_read(entries, 0, array_class) || !array_class ||
        !safe_read(entries, 24, array_length) || array_length > 4096) {
        return snapshot;
    }
    Il2CppClass* entry_class = g_api.class_get_element_class(array_class);
    std::int32_t entry_size = 0;
    std::size_t value_offset = 0;
    std::size_t hash_offset = 0;
    if (!entry_class || !safe_class_value_size(entry_class, &entry_size) ||
        !find_field_offset(entry_class, "value", nullptr, value_offset)) {
        return snapshot;
    }
    const bool has_hash =
        find_field_offset(entry_class, "hashCode", "_hashCode", hash_offset);
    constexpr std::size_t kValueTypeObjectHeader = 16;
    if (value_offset >= kValueTypeObjectHeader) {
        value_offset -= kValueTypeObjectHeader;
    }
    if (has_hash && hash_offset >= kValueTypeObjectHeader) {
        hash_offset -= kValueTypeObjectHeader;
    }
    if (value_offset + sizeof(void*) > static_cast<std::size_t>(entry_size) ||
        (has_hash && hash_offset + sizeof(std::int32_t) >
                         static_cast<std::size_t>(entry_size))) {
        return snapshot;
    }
    const std::size_t used = (std::min)(
        static_cast<std::size_t>(reported_count), array_length);
    auto* vector = static_cast<unsigned char*>(entries) + 32;
    for (std::size_t index = 0;
         index < used && snapshot.count < snapshot.objects.size(); ++index) {
        void* entry = vector + index * static_cast<std::size_t>(entry_size);
        std::int32_t hash_code = 0;
        void* value = nullptr;
        if ((has_hash && (!safe_read(entry, hash_offset, hash_code) ||
                          hash_code < 0)) ||
            !safe_read(entry, value_offset, value) || !value) {
            continue;
        }
        snapshot.objects[snapshot.count++] = value;
    }
    snapshot.valid = true;
    return snapshot;
}

struct IntListItems {
    std::array<std::int32_t, 128> values{};
    std::size_t count{};
    bool valid{};
};

IntListItems read_int_list(void* list) {
    IntListItems snapshot{};
    Il2CppClass* list_class = nullptr;
    std::size_t items_offset = 0;
    std::size_t size_offset = 0;
    void* items = nullptr;
    std::int32_t size = 0;
    std::size_t array_length = 0;
    if (!safe_read(list, 0, list_class) || !list_class ||
        !find_field_offset(list_class, "_items", "items", items_offset) ||
        !find_field_offset(list_class, "_size", "size", size_offset) ||
        !safe_read(list, items_offset, items) || !items ||
        !safe_read(list, size_offset, size) || size < 0 || size > 4096 ||
        !safe_read(items, 24, array_length) || array_length > 4096) {
        return snapshot;
    }
    const std::size_t used = (std::min)(
        static_cast<std::size_t>(size), array_length);
    auto* vector = static_cast<unsigned char*>(items) + 32;
    for (std::size_t index = 0;
         index < used && snapshot.count < snapshot.values.size(); ++index) {
        std::int32_t value = 0;
        if (safe_read(vector, index * sizeof(std::int32_t), value)) {
            snapshot.values[snapshot.count++] = value;
        }
    }
    snapshot.valid = true;
    return snapshot;
}

ObjectListItems read_object_list(void* list) {
    ObjectListItems snapshot{};
    Il2CppClass* list_class = nullptr;
    std::size_t items_offset = 0;
    std::size_t size_offset = 0;
    void* items = nullptr;
    std::int32_t size = 0;
    std::size_t array_length = 0;
    if (!safe_read(list, 0, list_class) || !list_class ||
        !find_field_offset(list_class, "_items", "items", items_offset) ||
        !find_field_offset(list_class, "_size", "size", size_offset) ||
        !safe_read(list, items_offset, items) || !items ||
        !safe_read(list, size_offset, size) || size < 0 || size > 4096 ||
        !safe_read(items, 24, array_length) || array_length > 4096) {
        return snapshot;
    }
    const std::size_t used = (std::min)(
        static_cast<std::size_t>(size), array_length);
    auto* vector = static_cast<unsigned char*>(items) + 32;
    for (std::size_t index = 0;
         index < used && snapshot.count < snapshot.objects.size(); ++index) {
        void* object = nullptr;
        if (safe_read(vector, index * sizeof(void*), object) && object) {
            snapshot.objects[snapshot.count++] = object;
        }
    }
    snapshot.valid = true;
    return snapshot;
}

void remember_skill_data_locked(void* skill_data) {
    if (!skill_data) {
        return;
    }
    std::int32_t incoming_skill_id = -1;
    std::int32_t incoming_hero_type_id = 0;
    const bool incoming_key_valid =
        safe_read(skill_data, 16, incoming_skill_id) &&
        safe_read(skill_data, 36, incoming_hero_type_id) &&
        incoming_skill_id >= 0 && incoming_hero_type_id > 0;
    std::size_t replacement = g_active_skill_data_count;
    for (std::size_t index = 0; index < g_active_skill_data_count; ++index) {
        void* existing = g_active_skill_data[index];
        if (existing == skill_data) {
            replacement = index;
            break;
        }
        if (!incoming_key_valid || !existing) {
            continue;
        }
        std::int32_t existing_skill_id = -1;
        std::int32_t existing_hero_type_id = 0;
        if (safe_read(existing, 16, existing_skill_id) &&
            safe_read(existing, 36, existing_hero_type_id) &&
            existing_skill_id == incoming_skill_id &&
            existing_hero_type_id == incoming_hero_type_id) {
            replacement = index;
            break;
        }
    }
    if (replacement < g_active_skill_data_count) {
        g_active_skill_data[replacement] = skill_data;
    } else if (g_active_skill_data_count < g_active_skill_data.size()) {
        g_active_skill_data[g_active_skill_data_count++] = skill_data;
    }
}

void remember_active_skill_data(void* list) {
    const ObjectListItems items = read_object_list(list);
    AcquireSRWLockExclusive(&g_active_skill_lock);
    for (std::size_t index = 0; index < items.count; ++index) {
        remember_skill_data_locked(items.objects[index]);
    }
    ReleaseSRWLockExclusive(&g_active_skill_lock);
}

void remember_single_skill_data(void* skill_data) {
    if (!skill_data) {
        return;
    }
    AcquireSRWLockExclusive(&g_active_skill_lock);
    remember_skill_data_locked(skill_data);
    ReleaseSRWLockExclusive(&g_active_skill_lock);
}

ObjectListItems active_skill_data_snapshot() {
    ObjectListItems snapshot{};
    AcquireSRWLockShared(&g_active_skill_lock);
    snapshot.count = (std::min)(g_active_skill_data_count,
                                snapshot.objects.size());
    for (std::size_t index = 0; index < snapshot.count; ++index) {
        snapshot.objects[index] = g_active_skill_data[index];
    }
    snapshot.valid = snapshot.count > 0;
    ReleaseSRWLockShared(&g_active_skill_lock);
    return snapshot;
}

void clear_active_skill_data() {
    AcquireSRWLockExclusive(&g_active_skill_lock);
    g_active_skill_data.fill(nullptr);
    g_active_skill_data_count = 0;
    ReleaseSRWLockExclusive(&g_active_skill_lock);
    g_skill_catalog_active_hero_id.store(INT_MIN, std::memory_order_release);
    g_skill_catalog_active_hero_type_id.store(INT_MIN,
                                              std::memory_order_release);
    g_skill_catalog_active_hero_turn_count.store(INT_MIN,
                                                 std::memory_order_release);
    g_skill_catalog_active_hero_form_index.store(INT_MIN,
                                                 std::memory_order_release);
    g_skill_catalog_active_hero_skills_update_counter.store(
        INT_MIN, std::memory_order_release);
}

void diagnostic_dictionary_keys(const char* event, void* dictionary) {
    const IntDictionaryKeys keys = read_int_dictionary_keys(dictionary);
    std::ostringstream message;
    message << event << " dictionary=" << dictionary
            << " valid=" << (keys.valid ? 1 : 0)
            << " stage=" << keys.stage
            << " reported_count=" << keys.reported_count
            << " entries_offset=" << keys.entries_offset
            << " count_offset=" << keys.count_offset
            << " array_length=" << keys.array_length
            << " entry_size=" << keys.entry_size
            << " key_offset=" << keys.key_offset
            << " hash_offset=" << keys.hash_offset << " keys=[";
    for (std::size_t index = 0; index < keys.count; ++index) {
        if (index) {
            message << ',';
        }
        message << keys.values[index];
    }
    message << ']';
    diagnostic(message.str());
}

ResolvedText resolve_object_name(void* object);

bool safe_runtime_invoke_object(const MethodInfo* method, void* instance,
                                void** result) {
    if (!method || !result) {
        return false;
    }
    __try {
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, instance, nullptr, &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

void* refresh_app_model_instance() {
    void* latest = nullptr;
    if (safe_runtime_invoke_object(g_app_model_instance_method, nullptr,
                                   &latest) && latest) {
        g_app_model_instance.store(latest, std::memory_order_release);
        return latest;
    }
    return g_app_model_instance.load(std::memory_order_acquire);
}

bool safe_runtime_invoke_one_bool_object(const MethodInfo* method,
                                         void* instance, bool value,
                                         void** result) {
    if (!method || !instance || !result) {
        return false;
    }
    __try {
        void* parameters[1] = {&value};
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, instance, parameters, &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

bool safe_runtime_lookup_by_id(const MethodInfo* method, void* instance,
                               std::int32_t type_id, void** result) {
    if (!method || !instance || !result) {
        return false;
    }
    __try {
        bool throw_if_missing = false;
        void* parameters[2] = {&type_id, &throw_if_missing};
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, instance, parameters, &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

bool safe_runtime_invoke_avatar_url(const MethodInfo* method, void* instance,
                                    std::int32_t form_index, void** result) {
    if (!method || !instance || !result) {
        return false;
    }
    __try {
        // A zeroed Nullable<int> represents a missing skin id.  Keeping both the
        // value and has-value storage zero also makes this independent of the
        // generated field order used by this Unity/IL2CPP build.
        std::uint64_t nullable_skin_id = 0;
        void* parameters[2] = {&nullable_skin_id, &form_index};
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, instance, parameters, &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

void* static_data_section(const char* field_name) {
    void* static_data = g_static_data.load(std::memory_order_acquire);
    Il2CppClass* static_data_class = nullptr;
    std::size_t field_offset = 0;
    void* section = nullptr;
    if (!safe_read(static_data, 0, static_data_class) || !static_data_class ||
        !find_field_offset(static_data_class, field_name, nullptr, field_offset) ||
        !safe_read(static_data, field_offset, section)) {
        return nullptr;
    }
    return section;
}

bool read_nullable_int_field(void* object, const char* field_name,
                             bool& has_value, std::int32_t& value) {
    has_value = false;
    value = 0;
    Il2CppClass* klass = nullptr;
    std::size_t offset = 0;
    if (!safe_read(object, 0, klass) || !klass ||
        !find_field_offset(klass, field_name, nullptr, offset)) {
        return false;
    }
    bool present = false;
    std::int32_t candidate = 0;
    if (!safe_read(object, offset, present) ||
        !safe_read(object, offset + 4, candidate)) {
        return false;
    }
    has_value = present;
    value = candidate;
    return true;
}

void append_int_list(std::ostringstream& output, const IntListItems& values) {
    output << '[';
    for (std::size_t index = 0; index < values.count; ++index) {
        if (index) {
            output << ',';
        }
        output << values.values[index];
    }
    output << ']';
}

void* find_chimera_challenge_reward(void* chimera_type,
                                    std::int32_t form,
                                    std::int32_t part,
                                    std::int32_t difficulty) {
    void* reward_groups_list = nullptr;
    if (!safe_read_field(chimera_type, "ChimeraChallengeRewards", nullptr,
                         reward_groups_list) ||
        !reward_groups_list) {
        return nullptr;
    }
    const ObjectListItems reward_groups =
        read_object_list(reward_groups_list);
    for (std::size_t index = 0; index < reward_groups.count; ++index) {
        void* group = reward_groups.objects[index];
        std::int32_t group_form = -1;
        std::int32_t group_part = -1;
        void* rewards_by_difficulty = nullptr;
        if (!safe_read_field(group, "Form", nullptr, group_form) ||
            !safe_read_field(group, "Part", nullptr, group_part) ||
            group_form != form || group_part != part ||
            !safe_read_field(group, "RewardsByChallengeDifficulty", nullptr,
                             rewards_by_difficulty) ||
            !rewards_by_difficulty) {
            continue;
        }
        const IntObjectDictionaryItems rewards =
            read_int_object_dictionary(rewards_by_difficulty);
        for (std::size_t reward_index = 0;
             reward_index < rewards.count; ++reward_index) {
            if (rewards.keys[reward_index] == difficulty) {
                return rewards.objects[reward_index];
            }
        }
    }
    return nullptr;
}

void append_flexible_reward(std::ostringstream& output, void* reward,
                            Il2CppClass* flexible_reward_type_class,
                            Il2CppClass* resource_type_id_class) {
    if (!reward) {
        output << "null";
        return;
    }
    std::int32_t roll_count = 0;
    void* rewards_list = nullptr;
    safe_read_field(reward, "Count", nullptr, roll_count);
    safe_read_field(reward, "Rewards", nullptr, rewards_list);
    const ObjectListItems entries = read_object_list(rewards_list);
    output << "{\"rollCount\":" << roll_count << ",\"entries\":[";
    for (std::size_t index = 0; index < entries.count; ++index) {
        if (index) {
            output << ',';
        }
        void* entry = entries.objects[index];
        std::int32_t type = 0;
        std::int32_t min_count = 0;
        std::int32_t max_count = 0;
        double probability = 0.0;
        bool has_resource = false;
        std::int32_t resource_type = 0;
        bool has_market_item = false;
        std::int32_t market_item_id = 0;
        safe_read_field(entry, "Type", nullptr, type);
        safe_read_field(entry, "Probability", nullptr, probability);
        safe_read_field(entry, "MinCount", nullptr, min_count);
        safe_read_field(entry, "MaxCount", nullptr, max_count);
        read_nullable_int_field(entry, "ResourceTypeId", has_resource,
                                resource_type);
        read_nullable_int_field(entry, "BlackMarketItemId", has_market_item,
                                market_item_id);
        output << "{\"typeId\":" << type << ",\"type\":\""
               << json_escape(enum_member_name(flexible_reward_type_class,
                                               type))
               << "\",\"probability\":" << probability
               << ",\"minCount\":" << min_count
               << ",\"maxCount\":" << max_count;
        if (has_resource) {
            output << ",\"resourceTypeId\":" << resource_type
                   << ",\"resourceType\":\""
                   << json_escape(enum_member_name(resource_type_id_class,
                                                   resource_type))
                   << '"';
        }
        if (has_market_item) {
            output << ",\"blackMarketItemId\":" << market_item_id;
        }
        output << '}';
    }
    output << "]}";
}

void append_flexible_reward_signature(std::ostringstream& output,
                                      void* reward) {
    if (!reward) {
        output << "reward:null;";
        return;
    }
    std::int32_t roll_count = 0;
    void* rewards_list = nullptr;
    safe_read_field(reward, "Count", nullptr, roll_count);
    safe_read_field(reward, "Rewards", nullptr, rewards_list);
    const ObjectListItems entries = read_object_list(rewards_list);
    output << "rolls:" << roll_count << ':' << entries.count << ';';
    for (std::size_t index = 0; index < entries.count; ++index) {
        void* entry = entries.objects[index];
        std::int32_t type = 0;
        std::int32_t min_count = 0;
        std::int32_t max_count = 0;
        double probability = 0.0;
        bool has_resource = false;
        std::int32_t resource_type = 0;
        bool has_market_item = false;
        std::int32_t market_item_id = 0;
        safe_read_field(entry, "Type", nullptr, type);
        safe_read_field(entry, "Probability", nullptr, probability);
        safe_read_field(entry, "MinCount", nullptr, min_count);
        safe_read_field(entry, "MaxCount", nullptr, max_count);
        read_nullable_int_field(entry, "ResourceTypeId", has_resource,
                                resource_type);
        read_nullable_int_field(entry, "BlackMarketItemId", has_market_item,
                                market_item_id);
        output << type << ',' << probability << ',' << min_count << ','
               << max_count << ',' << (has_resource ? resource_type : -1)
               << ',' << (has_market_item ? market_item_id : -1) << ';';
    }
}

void append_static_chimera_signatures(
    std::ostringstream& trial_definitions,
    std::ostringstream& reward_rotation,
    std::ostringstream& attribute_rotation) {
    void* alliance_data = static_data_section("AllianceData");
    void* chimera_types_list = nullptr;
    if (!alliance_data ||
        !safe_read_field(alliance_data, "ChimeraTypes", nullptr,
                         chimera_types_list) ||
        !chimera_types_list) {
        trial_definitions << "unavailable";
        reward_rotation << "unavailable";
        attribute_rotation << "unavailable";
        return;
    }
    const ObjectListItems chimera_types =
        read_object_list(chimera_types_list);
    for (std::size_t type_index = 0; type_index < chimera_types.count;
         ++type_index) {
        void* chimera_type = chimera_types.objects[type_index];
        std::int32_t alliance_difficulty = 0;
        std::int64_t health = 0;
        void* stage_ids_list = nullptr;
        void* challenges_dictionary = nullptr;
        safe_read_field(chimera_type, "DifficultyId", nullptr,
                        alliance_difficulty);
        safe_read_field(chimera_type, "Health", nullptr, health);
        safe_read_field(chimera_type, "StageIds", nullptr, stage_ids_list);
        safe_read_field(chimera_type, "_challengeTypeById", nullptr,
                        challenges_dictionary);
        const IntListItems stage_ids = read_int_list(stage_ids_list);
        const IntObjectDictionaryItems challenges =
            read_int_object_dictionary(challenges_dictionary);
        attribute_rotation << "difficulty:" << alliance_difficulty
                           << ",health:" << health << ",stages:";
        for (std::size_t index = 0; index < stage_ids.count; ++index) {
            attribute_rotation << stage_ids.values[index] << ',';
        }
        attribute_rotation << ';';
        trial_definitions << "difficulty:" << alliance_difficulty
                          << ";trials:";
        reward_rotation << "difficulty:" << alliance_difficulty
                        << ";rewards:";
        for (std::size_t challenge_index = 0;
             challenge_index < challenges.count; ++challenge_index) {
            void* challenge_type = challenges.objects[challenge_index];
            std::int32_t skill_type_id = 0;
            std::int32_t form = 0;
            std::int32_t part = 0;
            std::int32_t challenge_difficulty = 0;
            void* effects_list = nullptr;
            safe_read_field(challenge_type, "SkillTypeId", nullptr,
                            skill_type_id);
            safe_read_field(challenge_type, "Form", nullptr, form);
            safe_read_field(challenge_type, "Part", nullptr, part);
            safe_read_field(challenge_type, "Difficulty", nullptr,
                            challenge_difficulty);
            safe_read_field(challenge_type, "Effects", nullptr,
                            effects_list);
            const IntListItems effects = read_int_list(effects_list);
            trial_definitions << challenges.keys[challenge_index] << ','
                              << skill_type_id << ',' << form << ',' << part
                              << ',' << challenge_difficulty << ",effects:";
            for (std::size_t effect_index = 0;
                 effect_index < effects.count; ++effect_index) {
                trial_definitions << effects.values[effect_index] << ',';
            }
            trial_definitions << ';';
            reward_rotation << "trial:"
                            << challenges.keys[challenge_index] << ';';
            append_flexible_reward_signature(
                reward_rotation,
                find_chimera_challenge_reward(
                    chimera_type, form, part, challenge_difficulty));
        }
    }
}

void append_static_chimera_catalog(
    std::ostringstream& output,
    Il2CppClass* alliance_difficulty_class,
    Il2CppClass* form_class,
    Il2CppClass* part_class,
    Il2CppClass* challenge_difficulty_class,
    Il2CppClass* status_effect_type_id_class,
    Il2CppClass* flexible_reward_type_class,
    Il2CppClass* resource_type_id_class,
    std::int32_t selected_alliance_difficulty = INT_MIN) {
    void* alliance_data = static_data_section("AllianceData");
    void* chimera_types_list = nullptr;
    if (!alliance_data ||
        !safe_read_field(alliance_data, "ChimeraTypes", nullptr,
                         chimera_types_list) ||
        !chimera_types_list) {
        output << "{\"available\":false,\"difficulties\":[]}";
        return;
    }
    const ObjectListItems chimera_types =
        read_object_list(chimera_types_list);
    output << "{\"available\":true,\"difficulties\":[";
    bool first_type = true;
    for (std::size_t type_index = 0; type_index < chimera_types.count;
         ++type_index) {
        void* chimera_type = chimera_types.objects[type_index];
        std::int32_t alliance_difficulty = 0;
        std::int64_t health = 0;
        void* stage_ids_list = nullptr;
        void* challenges_dictionary = nullptr;
        safe_read_field(chimera_type, "DifficultyId", nullptr,
                        alliance_difficulty);
        if (selected_alliance_difficulty != INT_MIN &&
            alliance_difficulty != selected_alliance_difficulty) {
            continue;
        }
        if (!first_type) {
            output << ',';
        }
        first_type = false;
        safe_read_field(chimera_type, "Health", nullptr, health);
        safe_read_field(chimera_type, "StageIds", nullptr, stage_ids_list);
        safe_read_field(chimera_type, "_challengeTypeById", nullptr,
                        challenges_dictionary);
        const IntListItems stage_ids = read_int_list(stage_ids_list);
        const IntObjectDictionaryItems challenges =
            read_int_object_dictionary(challenges_dictionary);
        output << "{\"difficultyId\":" << alliance_difficulty
               << ",\"difficulty\":\""
               << json_escape(enum_member_name(alliance_difficulty_class,
                                               alliance_difficulty))
               << "\",\"health\":" << health << ",\"stageIds\":";
        append_int_list(output, stage_ids);
        output << ",\"trials\":[";
        for (std::size_t challenge_index = 0;
             challenge_index < challenges.count; ++challenge_index) {
            if (challenge_index) {
                output << ',';
            }
            void* challenge_type = challenges.objects[challenge_index];
            std::int32_t skill_type_id = 0;
            std::int32_t form = 0;
            std::int32_t part = 0;
            std::int32_t challenge_difficulty = 0;
            void* effects_list = nullptr;
            void* description_object = nullptr;
            safe_read_field(challenge_type, "SkillTypeId", nullptr,
                            skill_type_id);
            safe_read_field(challenge_type, "Form", nullptr, form);
            safe_read_field(challenge_type, "Part", nullptr, part);
            safe_read_field(challenge_type, "Difficulty", nullptr,
                            challenge_difficulty);
            safe_read_field(challenge_type, "Effects", nullptr,
                            effects_list);
            safe_read_field(challenge_type, "Description", nullptr,
                            description_object);
            const IntListItems effects = read_int_list(effects_list);
            const ResolvedText description =
                resolve_shared_text(description_object);
            output << "{\"id\":" << challenges.keys[challenge_index]
                   << ",\"skillTypeId\":" << skill_type_id
                   << ",\"formId\":" << form << ",\"form\":\""
                   << json_escape(enum_member_name(form_class, form))
                   << "\",\"partId\":" << part << ",\"part\":\""
                   << json_escape(enum_member_name(part_class, part))
                   << "\",\"difficultyId\":" << challenge_difficulty
                   << ",\"difficulty\":\""
                   << json_escape(enum_member_name(
                          challenge_difficulty_class,
                          challenge_difficulty))
                   << "\",\"name\":\""
                   << json_escape(enum_member_name(form_class, form) + "/" +
                                  enum_member_name(part_class, part) + "/" +
                                  enum_member_name(challenge_difficulty_class,
                                                   challenge_difficulty))
                   << "\",\"descriptionKey\":\""
                   << json_escape(description.key)
                   << "\",\"description\":\""
                   << json_escape(description.display) << "\",\"effects\":[";
            for (std::size_t effect_index = 0;
                 effect_index < effects.count; ++effect_index) {
                if (effect_index) {
                    output << ',';
                }
                const std::int32_t effect_id = effects.values[effect_index];
                output << "{\"id\":" << effect_id << ",\"name\":\""
                       << json_escape(enum_member_name(
                              status_effect_type_id_class, effect_id))
                       << "\"}";
            }
            output << "],\"reward\":";
            append_flexible_reward(
                output,
                find_chimera_challenge_reward(
                    chimera_type, form, part, challenge_difficulty),
                flexible_reward_type_class, resource_type_id_class);
            output << '}';
        }
        output << "]}";
    }
    output << "]}";
}

std::string chimera_rotation_metadata_json() {
    void* alliance_data = static_data_section("AllianceData");
    void* sequence_dictionary = nullptr;
    std::int32_t turns_between_forms = 0;
    if (alliance_data) {
        safe_read_field(alliance_data, "ChimeraSequenceForms", nullptr,
                        sequence_dictionary);
        safe_read_field(alliance_data, "ChimeraTurnsCountBetweenForm", nullptr,
                        turns_between_forms);
    }
    IntIntDictionaryItems sequence =
        read_int_int_dictionary(sequence_dictionary);
    for (std::size_t left = 0; left < sequence.count; ++left) {
        for (std::size_t right = left + 1; right < sequence.count; ++right) {
            if (sequence.keys[right] < sequence.keys[left]) {
                (std::swap)(sequence.keys[left], sequence.keys[right]);
                (std::swap)(sequence.values[left], sequence.values[right]);
            }
        }
    }
    std::ostringstream output;
    output << "{\"turnsBetweenForms\":" << turns_between_forms
           << ",\"formSequence\":[";
    for (std::size_t index = 0; index < sequence.count; ++index) {
        if (index) {
            output << ',';
        }
        output << "{\"sequenceIndex\":" << sequence.keys[index]
               << ",\"formId\":" << sequence.values[index]
               << ",\"form\":\""
               << json_escape(enum_member_name(g_chimera_form_class,
                                               sequence.values[index]))
               << "\"}";
    }
    output << "]}";
    return output.str();
}

std::uint64_t fnv1a64(const std::string& value) {
    std::uint64_t hash = 14695981039346656037ULL;
    for (const unsigned char byte : value) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string fingerprint_text(std::uint64_t fingerprint) {
    std::ostringstream output;
    output << "fnv1a64:" << std::hex << std::setfill('0') << std::setw(16)
           << fingerprint;
    return output.str();
}

bool refresh_chimera_rotation_catalog(bool force) {
    std::ostringstream full_catalog;
    append_static_chimera_catalog(
        full_catalog, g_alliance_chimera_difficulty_class,
        g_chimera_form_class, g_chimera_challenge_part_class,
        g_chimera_challenge_difficulty_class,
        g_status_effect_type_id_class, g_flexible_reward_type_class,
        g_resource_type_id_class);
    const std::string catalog_json = full_catalog.str();
    const std::string status_effects_json =
        enum_catalog_json(g_status_effect_type_id_class);
    const std::string metadata_json = chimera_rotation_metadata_json();
    std::ostringstream trial_definition_signature;
    std::ostringstream reward_rotation_signature;
    std::ostringstream attribute_rotation_signature;
    append_static_chimera_signatures(
        trial_definition_signature, reward_rotation_signature,
        attribute_rotation_signature);
    const std::string trial_definition_fingerprint = fingerprint_text(
        fnv1a64(trial_definition_signature.str()));
    const std::string reward_rotation_fingerprint = fingerprint_text(
        fnv1a64(reward_rotation_signature.str()));
    const std::string attribute_rotation_fingerprint = fingerprint_text(
        fnv1a64(attribute_rotation_signature.str() + "|" + metadata_json));
    const std::string fingerprint = fingerprint_text(fnv1a64(
        trial_definition_fingerprint + "|" + reward_rotation_fingerprint +
        "|" + attribute_rotation_fingerprint + "|" + status_effects_json));

    AcquireSRWLockShared(&g_chimera_catalog_lock);
    const bool unchanged = fingerprint == g_chimera_rotation_fingerprint;
    ReleaseSRWLockShared(&g_chimera_catalog_lock);
    if (!force && unchanged) {
        return false;
    }

    std::array<std::string, 7> by_difficulty{};
    for (std::int32_t difficulty = 1;
         difficulty < static_cast<std::int32_t>(by_difficulty.size());
         ++difficulty) {
        std::ostringstream selected;
        append_static_chimera_catalog(
            selected, g_alliance_chimera_difficulty_class,
            g_chimera_form_class, g_chimera_challenge_part_class,
            g_chimera_challenge_difficulty_class,
            g_status_effect_type_id_class, g_flexible_reward_type_class,
            g_resource_type_id_class, difficulty);
        by_difficulty[difficulty] = selected.str();
    }

    std::ostringstream identity;
    identity << "{\"catalogFingerprint\":\""
             << json_escape(fingerprint)
             << "\",\"trialDefinitionFingerprint\":\""
             << json_escape(trial_definition_fingerprint)
             << "\",\"rewardRotationFingerprint\":\""
             << json_escape(reward_rotation_fingerprint)
             << "\",\"attributeRotationFingerprint\":\""
             << json_escape(attribute_rotation_fingerprint)
             << "\",\"metadata\":"
             << metadata_json << '}';
    std::ostringstream payload;
    payload << "{\"type\":\"chimera_rotation_catalog\",\"schemaVersion\":2"
            << ",\"pid\":" << GetCurrentProcessId()
            << ",\"observedAtTick\":" << GetTickCount64()
            << ",\"identity\":" << identity.str()
             << ",\"catalog\":" << catalog_json
             << ",\"statusEffects\":" << status_effects_json
             << ",\"hydraHeads\":";
    append_static_hydra_head_catalog(payload);
    payload << '}';

    AcquireSRWLockExclusive(&g_chimera_catalog_lock);
    g_chimera_catalog_by_difficulty = std::move(by_difficulty);
    g_chimera_rotation_identity_json = identity.str();
    g_chimera_rotation_fingerprint = fingerprint;
    ReleaseSRWLockExclusive(&g_chimera_catalog_lock);
    if (g_shared_state) {
        publish_shared_json(g_shared_state->rotation_catalog, payload.str());
    }
    diagnostic("chimera_rotation_catalog_published fingerprint=" +
               fingerprint + " bytes=" +
               std::to_string(payload.str().size()));
    return true;
}

struct ChimeraTrialStaticIdentity {
    std::int32_t difficulty_id{};
    std::int32_t form_id{};
    std::int32_t part_id{};
    std::int32_t challenge_difficulty_id{};
};

ChimeraTrialStaticIdentity alliance_chimera_trial_identity(
    std::int32_t trial_id) {
    if (trial_id <= 0) {
        return {};
    }
    void* alliance_data = static_data_section("AllianceData");
    void* chimera_types_list = nullptr;
    if (!alliance_data ||
        !safe_read_field(alliance_data, "ChimeraTypes", nullptr,
                         chimera_types_list) ||
        !chimera_types_list) {
        return {};
    }
    const ObjectListItems chimera_types =
        read_object_list(chimera_types_list);
    for (std::size_t index = 0; index < chimera_types.count; ++index) {
        void* chimera_type = chimera_types.objects[index];
        void* challenges_dictionary = nullptr;
        std::int32_t difficulty = 0;
        if (!safe_read_field(chimera_type, "DifficultyId", nullptr,
                             difficulty) ||
            !safe_read_field(chimera_type, "_challengeTypeById", nullptr,
                             challenges_dictionary) ||
            !challenges_dictionary) {
            continue;
        }
        const IntObjectDictionaryItems challenges =
            read_int_object_dictionary(challenges_dictionary);
        for (std::size_t challenge_index = 0;
             challenge_index < challenges.count; ++challenge_index) {
            if (challenges.keys[challenge_index] == trial_id) {
                std::int32_t form = 0;
                std::int32_t part = 0;
                std::int32_t challenge_difficulty = 0;
                safe_read_field(challenges.objects[challenge_index], "Form",
                                nullptr, form);
                safe_read_field(challenges.objects[challenge_index], "Part",
                                nullptr, part);
                safe_read_field(challenges.objects[challenge_index],
                                "Difficulty", nullptr,
                                challenge_difficulty);
                return {difficulty, form, part, challenge_difficulty};
            }
        }
    }
    return {};
}

std::int32_t alliance_chimera_difficulty_for_trial_id(
    std::int32_t trial_id) {
    return alliance_chimera_trial_identity(trial_id).difficulty_id;
}

std::int32_t chimera_last_rotation_turn_for_form(std::int32_t form_id) {
    if (form_id <= 0) {
        return 0;
    }
    void* alliance_data = static_data_section("AllianceData");
    void* sequence_dictionary = nullptr;
    if (!alliance_data ||
        !safe_read_field(alliance_data, "ChimeraSequenceForms", nullptr,
                         sequence_dictionary) ||
        !sequence_dictionary) {
        return 0;
    }
    const IntIntDictionaryItems sequence =
        read_int_int_dictionary(sequence_dictionary);
    std::int32_t last_turn = 0;
    for (std::size_t index = 0; index < sequence.count; ++index) {
        if (sequence.values[index] == form_id) {
            last_turn = (std::max)(last_turn, sequence.keys[index]);
        }
    }
    return last_turn;
}

ResolvedText resolve_static_type_name(const char* section_field,
                                      const char* getter,
                                      std::int32_t type_id) {
    void* section = static_data_section(section_field);
    Il2CppClass* section_class = nullptr;
    if (!safe_read(section, 0, section_class) || !section_class) {
        return {};
    }
    const MethodInfo* method =
        g_api.class_get_method_from_name(section_class, getter, 2);
    void* type_object = nullptr;
    if (!safe_runtime_lookup_by_id(method, section, type_id, &type_object)) {
        return {};
    }
    return resolve_object_name(type_object);
}

void* find_static_hero_type(std::int32_t hero_type_id) {
    void* section = static_data_section("HeroData");
    Il2CppClass* section_class = nullptr;
    if (!safe_read(section, 0, section_class) || !section_class) {
        return nullptr;
    }

    // Team selection exposes hero type ids before a battle model exists.
    // Resolve them from StaticHeroData's already-populated dictionary first;
    // this is both cheaper and more reliable than a managed runtime invoke.
    void* dictionary = nullptr;
    if (safe_read_field(section, "HeroTypeById", nullptr, dictionary) &&
        dictionary) {
        const IntObjectDictionaryItems hero_types =
            read_int_object_dictionary(dictionary);
        for (std::size_t index = 0; index < hero_types.count; ++index) {
            if (hero_types.keys[index] == hero_type_id) {
                return hero_types.objects[index];
            }
        }
    }

    const MethodInfo* method =
        g_api.class_get_method_from_name(section_class, "GetHeroType", 2);
    void* hero_type = nullptr;
    return safe_runtime_lookup_by_id(method, section, hero_type_id, &hero_type)
               ? hero_type
               : nullptr;
}

std::string resolve_hero_avatar(void* hero_type,
                                std::int32_t form_index) {
    Il2CppClass* hero_class = nullptr;
    if (!safe_read(hero_type, 0, hero_class) || !hero_class) {
        return {};
    }
    const MethodInfo* method =
        g_api.class_get_method_from_name(hero_class, "AvatarUrl", 2);
    void* value = nullptr;
    if (!safe_runtime_invoke_avatar_url(method, hero_type, form_index, &value)) {
        return {};
    }
    return il2cpp_string_utf8(value);
}

std::string resolve_static_hero_avatar(std::int32_t hero_type_id,
                                       std::int32_t form_index) {
    return resolve_hero_avatar(find_static_hero_type(hero_type_id),
                               form_index);
}

ResolvedText resolve_static_hero_name(std::int32_t hero_type_id) {
    return resolve_static_type_name("HeroData", "GetHeroType", hero_type_id);
}

ResolvedText resolve_static_skill_name(std::int32_t skill_type_id) {
    return resolve_static_type_name("SkillData", "GetSkillType", skill_type_id);
}

ResolvedText resolve_object_name(void* object) {
    Il2CppClass* klass = nullptr;
    std::size_t name_offset = 0;
    void* shared_name = nullptr;
    if (!safe_read(object, 0, klass) || !klass ||
        !find_field_offset(klass, "Name", nullptr, name_offset) ||
        !safe_read(object, name_offset, shared_name)) {
        return {};
    }
    return resolve_shared_text(shared_name);
}

ResolvedText resolve_object_description(void* object) {
    Il2CppClass* klass = nullptr;
    std::size_t description_offset = 0;
    void* shared_description = nullptr;
    if (!safe_read(object, 0, klass) || !klass ||
        !find_field_offset(klass, "Description", nullptr,
                           description_offset) ||
        !safe_read(object, description_offset, shared_description)) {
        return {};
    }
    return resolve_shared_text(shared_description);
}

void* find_skill_type(void* hero_type, std::int32_t wanted_type_id) {
    Il2CppClass* hero_class = nullptr;
    if (!safe_read(hero_type, 0, hero_class) || !hero_class) {
        return nullptr;
    }
    const MethodInfo* get_all_skills =
        g_api.class_get_method_from_name(hero_class, "get_AllSkillTypes", 0);
    void* list = nullptr;
    if (!safe_runtime_invoke_object(get_all_skills, hero_type, &list)) {
        return nullptr;
    }
    Il2CppClass* list_class = nullptr;
    std::size_t items_offset = 0;
    std::size_t size_offset = 0;
    void* items = nullptr;
    std::int32_t size = 0;
    std::size_t array_length = 0;
    if (!safe_read(list, 0, list_class) || !list_class ||
        !find_field_offset(list_class, "_items", "items", items_offset) ||
        !find_field_offset(list_class, "_size", "size", size_offset) ||
        !safe_read(list, items_offset, items) || !items ||
        !safe_read(list, size_offset, size) || size < 0 || size > 64 ||
        !safe_read(items, 24, array_length)) {
        return nullptr;
    }
    const std::size_t used = (std::min)(static_cast<std::size_t>(size),
                                        array_length);
    auto* vector = static_cast<unsigned char*>(items) + 32;
    for (std::size_t index = 0; index < used; ++index) {
        void* skill_type = nullptr;
        Il2CppClass* skill_class = nullptr;
        std::size_t id_offset = 0;
        std::int32_t type_id = 0;
        if (!safe_read(vector, index * sizeof(void*), skill_type) ||
            !safe_read(skill_type, 0, skill_class) || !skill_class ||
            !find_field_offset(skill_class, "Id", nullptr, id_offset) ||
            !safe_read(skill_type, id_offset, type_id)) {
            continue;
        }
        if (type_id == wanted_type_id) {
            return skill_type;
        }
    }
    return nullptr;
}

void diagnostic_actor_names(const char* event, void* dictionary,
                            std::int32_t wanted_skill_type_id) {
    const IntObjectDictionaryItems items =
        read_int_object_dictionary(dictionary);
    if (!items.valid) {
        diagnostic(std::string(event) + " names_unavailable=1");
        return;
    }
    for (std::size_t index = 0; index < items.count; ++index) {
        void* actor = items.objects[index];
        Il2CppClass* actor_class = nullptr;
        const char* actor_class_name = "";
        std::int32_t hero_type_id = 0;
        void* hero_type = nullptr;
        if (safe_read(actor, 0, actor_class) && actor_class) {
            actor_class_name = g_api.class_get_name(actor_class);
            std::size_t hero_type_id_offset = 0;
            if (find_field_offset(actor_class, "HeroTypeId",
                                  "<HeroTypeId>k__BackingField",
                                  hero_type_id_offset)) {
                safe_read(actor, hero_type_id_offset, hero_type_id);
            }
            std::size_t hero_type_offset = 0;
            if (find_field_offset(actor_class, "_heroType", nullptr,
                                  hero_type_offset)) {
                safe_read(actor, hero_type_offset, hero_type);
            }
        }

        ResolvedText hero_name{};
        ResolvedText skill_name{};
        if (hero_type) {
            hero_name = resolve_object_name(hero_type);
            void* skill_type = find_skill_type(hero_type, wanted_skill_type_id);
            if (skill_type) {
                skill_name = resolve_object_name(skill_type);
            }
        }
        if (hero_name.display.empty() && hero_type_id > 0) {
            hero_name = resolve_static_hero_name(hero_type_id);
        }

        std::ostringstream message;
        message << event << " actor_id=" << items.keys[index]
                << " actor_class=" << (actor_class_name ? actor_class_name : "")
                << " hero_type_id=" << hero_type_id
                << " hero_name_key=\"" << hero_name.key << "\""
                << " hero_name=\"" << hero_name.display << "\"";
        if (!skill_name.key.empty() || !skill_name.display.empty()) {
            message << " skill_type_id=" << wanted_skill_type_id
                    << " skill_name_key=\"" << skill_name.key << "\""
                    << " skill_name=\"" << skill_name.display << "\"";
        }
        diagnostic(message.str());
    }
}

bool object_has_class_name(void* object, const char* expected) {
    Il2CppClass* klass = nullptr;
    if (!safe_read(object, 0, klass) || !klass || !g_api.class_get_name) {
        return false;
    }
    __try {
        const char* actual = g_api.class_get_name(klass);
        return actual && std::strcmp(actual, expected) == 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool object_derives_from(void* object, const char* expected) {
    Il2CppClass* klass = nullptr;
    if (!safe_read(object, 0, klass) || !klass || !g_api.class_get_name) {
        return false;
    }
    __try {
        for (int depth = 0; klass && depth < 16; ++depth) {
            const char* actual = g_api.class_get_name(klass);
            if (actual && std::strcmp(actual, expected) == 0) {
                return true;
            }
            klass = g_api.class_get_parent(klass);
        }
        return false;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool dictionary_contains(const IntDictionaryKeys& keys, std::int32_t wanted) {
    if (!keys.valid) {
        return false;
    }
    for (std::size_t index = 0; index < keys.count; ++index) {
        if (keys.values[index] == wanted) {
            return true;
        }
    }
    return false;
}

bool runtime_get_int(const MethodInfo* method, void* instance,
                     std::int32_t* value) {
    __try {
        void* exception = nullptr;
        void* result = g_api.runtime_invoke(method, instance, nullptr, &exception);
        if (exception || !result) {
            return false;
        }
        void* unboxed = g_api.object_unbox(result);
        if (!unboxed) {
            return false;
        }
        *value = *reinterpret_cast<std::int32_t*>(unboxed);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool runtime_get_bool(const MethodInfo* method, void* instance, bool* value) {
    __try {
        void* exception = nullptr;
        void* result = g_api.runtime_invoke(method, instance, nullptr, &exception);
        if (exception || !result) {
            return false;
        }
        void* unboxed = g_api.object_unbox(result);
        if (!unboxed) {
            return false;
        }
        *value = *reinterpret_cast<bool*>(unboxed);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool raw_runtime_invoke_void(const MethodInfo* method, void* instance,
                             void** managed_exception) {
    if (!method || !instance || !managed_exception) {
        return false;
    }
    *managed_exception = nullptr;
    __try {
        g_api.runtime_invoke(method, instance, nullptr, managed_exception);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool raw_format_runtime_exception(void* exception, char* message,
                                  int message_size, char* stack,
                                  int stack_size) {
    __try {
        if (g_api.format_exception) {
            g_api.format_exception(exception, message, message_size);
        }
        if (g_api.format_stack_trace) {
            g_api.format_stack_trace(exception, stack, stack_size);
        }
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool safe_runtime_invoke_void(const MethodInfo* method, void* instance) {
    void* exception = nullptr;
    if (!raw_runtime_invoke_void(method, instance, &exception)) {
        diagnostic("runtime_invoke_void_native_exception");
        return false;
    }
    if (!exception) {
        return true;
    }
    std::array<char, 2048> message{};
    std::array<char, 4096> stack{};
    raw_format_runtime_exception(exception, message.data(),
                                 static_cast<int>(message.size()),
                                 stack.data(), static_cast<int>(stack.size()));
    diagnostic(std::string("runtime_invoke_void_managed_exception message=\"") +
               json_escape(message.data()) + "\" stack=\"" +
               json_escape(stack.data()) + "\"");
    return false;
}

bool raw_runtime_invoke_one_bool_void(const MethodInfo* method, void* instance,
                                      bool value, void** managed_exception) {
    if (!method || !instance || !managed_exception) {
        return false;
    }
    *managed_exception = nullptr;
    __try {
        void* parameters[1] = {&value};
        g_api.runtime_invoke(method, instance, parameters, managed_exception);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool safe_runtime_invoke_one_bool_void(const MethodInfo* method,
                                       void* instance, bool value) {
    void* exception = nullptr;
    if (!raw_runtime_invoke_one_bool_void(method, instance, value,
                                          &exception)) {
        diagnostic("runtime_invoke_bool_native_exception");
        return false;
    }
    if (!exception) {
        return true;
    }
    std::array<char, 2048> message{};
    std::array<char, 4096> stack{};
    raw_format_runtime_exception(exception, message.data(),
                                 static_cast<int>(message.size()),
                                 stack.data(), static_cast<int>(stack.size()));
    diagnostic(std::string("runtime_invoke_bool_managed_exception message=\"") +
               json_escape(message.data()) + "\" stack=\"" +
               json_escape(stack.data()) + "\"");
    return false;
}

bool raw_runtime_invoke_two_bool_void(const MethodInfo* method, void* instance,
                                      bool first, bool second,
                                      void** managed_exception) {
    if (!method || !instance || !managed_exception) {
        return false;
    }
    *managed_exception = nullptr;
    __try {
        void* parameters[2] = {&first, &second};
        g_api.runtime_invoke(method, instance, parameters, managed_exception);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool safe_runtime_invoke_two_bool_void(const MethodInfo* method,
                                       void* instance, bool first,
                                       bool second) {
    void* exception = nullptr;
    if (!raw_runtime_invoke_two_bool_void(method, instance, first, second,
                                          &exception)) {
        diagnostic("runtime_invoke_two_bool_native_exception");
        return false;
    }
    if (!exception) {
        return true;
    }
    std::array<char, 2048> message{};
    std::array<char, 4096> stack{};
    raw_format_runtime_exception(exception, message.data(),
                                 static_cast<int>(message.size()),
                                 stack.data(), static_cast<int>(stack.size()));
    diagnostic(
        std::string("runtime_invoke_two_bool_managed_exception message=\"") +
        json_escape(message.data()) + "\" stack=\"" +
        json_escape(stack.data()) + "\"");
    return false;
}

bool raw_runtime_invoke_int_bool_void(const MethodInfo* method, void* instance,
                                      std::int32_t first, bool second,
                                      void** managed_exception) {
    if (!method || !instance || !managed_exception) {
        return false;
    }
    *managed_exception = nullptr;
    __try {
        void* parameters[2] = {&first, &second};
        g_api.runtime_invoke(method, instance, parameters, managed_exception);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool safe_runtime_invoke_int_bool_void(const MethodInfo* method,
                                       void* instance, std::int32_t first,
                                       bool second) {
    void* exception = nullptr;
    if (!raw_runtime_invoke_int_bool_void(method, instance, first, second,
                                          &exception)) {
        diagnostic("runtime_invoke_int_bool_native_exception");
        return false;
    }
    if (!exception) {
        return true;
    }
    std::array<char, 2048> message{};
    std::array<char, 4096> stack{};
    raw_format_runtime_exception(exception, message.data(),
                                 static_cast<int>(message.size()),
                                 stack.data(), static_cast<int>(stack.size()));
    diagnostic(
        std::string("runtime_invoke_int_bool_managed_exception message=\"") +
        json_escape(message.data()) + "\" stack=\"" +
        json_escape(stack.data()) + "\"");
    return false;
}

struct IntArraySnapshot {
    std::array<std::int32_t, 8> values{};
    std::size_t count{};
    bool valid{};
};

IntArraySnapshot read_int_array(void* array) {
    IntArraySnapshot snapshot{};
    std::size_t length = 0;
    if (!array || !safe_read(array, 24, length) || length > 64) {
        return snapshot;
    }
    snapshot.count = (std::min)(length, snapshot.values.size());
    auto* vector = static_cast<unsigned char*>(array) + 32;
    for (std::size_t index = 0; index < snapshot.count; ++index) {
        if (!safe_read(vector, index * sizeof(std::int32_t),
                       snapshot.values[index])) {
            snapshot = {};
            return snapshot;
        }
    }
    snapshot.valid = length <= snapshot.values.size();
    return snapshot;
}

bool runtime_get_fixed_raw(const MethodInfo* method, void* instance,
                           std::int64_t* raw_value) {
    if (!method || !raw_value) {
        return false;
    }
    __try {
        void* exception = nullptr;
        void* result = g_api.runtime_invoke(method, instance, nullptr, &exception);
        if (exception || !result) {
            return false;
        }
        void* unboxed = g_api.object_unbox(result);
        if (!unboxed) {
            return false;
        }
        *raw_value = *reinterpret_cast<std::int64_t*>(unboxed);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool runtime_get_fixed_raw_one_object(const MethodInfo* method,
                                      void* parameter,
                                      std::int64_t* raw_value) {
    if (!method || !parameter || !raw_value) {
        return false;
    }
    __try {
        void* parameters[1] = {parameter};
        void* exception = nullptr;
        void* result =
            g_api.runtime_invoke(method, nullptr, parameters, &exception);
        if (exception || !result) {
            return false;
        }
        void* unboxed = g_api.object_unbox(result);
        if (!unboxed) {
            return false;
        }
        *raw_value = *reinterpret_cast<std::int64_t*>(unboxed);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

const MethodInfo* find_hydra_total_damage_method(Il2CppClass* klass) {
    if (!klass) {
        return nullptr;
    }
    void* iterator = nullptr;
    while (const MethodInfo* method =
               g_api.class_get_methods(klass, &iterator)) {
        const char* name = g_api.method_get_name(method);
        if (!name || std::strcmp(name, "HydraTotalTakenDamage") != 0 ||
            g_api.method_get_param_count(method) != 1) {
            continue;
        }
        char* parameter_type =
            g_api.type_get_name(g_api.method_get_param(method, 0));
        const bool accepts_battle_hero = parameter_type &&
            std::strcmp(parameter_type,
                        "SharedModel.Battle.Core.Hero.BattleHero") == 0;
        if (parameter_type) {
            g_api.free(parameter_type);
        }
        if (accepts_battle_hero) {
            return method;
        }
    }
    return nullptr;
}

const MethodInfo* find_one_int_method(Il2CppClass* klass, const char* wanted) {
    for (int depth = 0; klass && depth < 8; ++depth) {
        void* iterator = nullptr;
        while (const MethodInfo* method =
                   g_api.class_get_methods(klass, &iterator)) {
            const char* name = g_api.method_get_name(method);
            if (!name || std::strcmp(name, wanted) != 0 ||
                g_api.method_get_param_count(method) != 1) {
                continue;
            }
            char* parameter_type =
                g_api.type_get_name(g_api.method_get_param(method, 0));
            const bool is_int = parameter_type &&
                std::strcmp(parameter_type, "System.Int32") == 0;
            if (parameter_type) {
                g_api.free(parameter_type);
            }
            if (is_int) {
                return method;
            }
        }
        klass = g_api.class_get_parent(klass);
    }
    return nullptr;
}

bool safe_runtime_invoke_one_int(const MethodInfo* method, void* instance,
                                 std::int32_t value, void** result) {
    if (!method || !instance || !result) {
        return false;
    }
    __try {
        void* parameters[1] = {&value};
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, instance, parameters, &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

bool safe_runtime_invoke_pointer_int(const MethodInfo* method, void* instance,
                                     void* pointer, std::int32_t value,
                                     void** result) {
    if (!method || !instance || !pointer || !result) {
        return false;
    }
    __try {
        void* parameters[2] = {pointer, &value};
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, instance, parameters, &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

bool safe_runtime_invoke_nullable_int_none(const MethodInfo* method,
                                           void* instance, void** result) {
    if (!method || !instance || !result) {
        return false;
    }
    __try {
        // A zero-initialized Nullable<Int32> represents null regardless of
        // whether this IL2CPP build lays out the value or hasValue field first.
        std::array<std::uint8_t, 8> nullable_int{};
        void* parameters[1] = {nullable_int.data()};
        void* exception = nullptr;
        *result = g_api.runtime_invoke(method, instance, parameters,
                                       &exception);
        return !exception && *result;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        *result = nullptr;
        return false;
    }
}

bool safe_call_create_manual(void* generator, std::int32_t target_id,
                             std::int32_t skill_id, bool* waiting_after) {
    __try {
        void* arguments[2] = {&target_id, &skill_id};
        void* exception = nullptr;
        g_api.runtime_invoke(g_create_manual_method, generator, arguments,
                             &exception);
        if (exception) {
            return false;
        }
        *waiting_after = false;
        safe_read(generator, 56, *waiting_after);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool safe_get_acceptable_targets(void* mode, void* skill_data,
                                 void** targets) {
    __try {
        *targets = g_original_get_acceptable_targets(
            mode, skill_data, g_acceptable_targets_method);
        return *targets != nullptr;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

std::int32_t canonical_runtime_hero_type_id(std::int32_t type_id) {
    // Runtime roster identities commonly use the trailing 6 variant while
    // SkillData keeps the catalog's trailing 0 identity.  They describe the
    // same champion and must not invalidate an otherwise exact turn guard.
    return type_id > 6 && type_id % 10 == 6 ? type_id - 6 : type_id;
}

bool is_chimera_type_id(std::int32_t type_id) {
    const std::int32_t canonical = canonical_runtime_hero_type_id(type_id);
    // The current game uses two Chimera catalog identities.  Runtime actors
    // expose their trailing-6 variants (26866/26876), while older guards only
    // accepted the first family.
    return canonical == 26860 || canonical == 26870;
}

struct AllianceBossIdentity {
    LONG mode{kTakeoverBossModeUnknown};
    LONG requested_mode{kTakeoverBossModeUnknown};
    bool started_selection_valid{};
    bool started_mode_matches{};
    bool active_hero_matches_team{};
    bool chimera_signal{};
    bool hydra_signal{};
    std::size_t boss_count{};
};

bool selection_contains_runtime_hero_type(
    const SelectionSnapshot& selection, std::int32_t active_hero_type_id) {
    if (active_hero_type_id <= 0) {
        return false;
    }
    const std::int32_t canonical_active =
        canonical_runtime_hero_type_id(active_hero_type_id);
    for (std::size_t index = 0; index < selection.hero_count; ++index) {
        if (canonical_runtime_hero_type_id(selection.hero_type_ids[index]) ==
            canonical_active) {
            return true;
        }
    }
    return false;
}

AllianceBossIdentity identify_alliance_boss(
    bool active_hero_valid, std::int32_t active_hero_type_id,
    bool chimera_preset, bool reported_hydra_battle,
    bool chimera_present, std::size_t boss_count) {
    AllianceBossIdentity identity{};
    identity.boss_count = boss_count;
    identity.chimera_signal = chimera_preset || chimera_present;
    identity.hydra_signal = reported_hydra_battle;
    if (g_takeover_state.load(std::memory_order_acquire) == 1) {
        identity.requested_mode =
            g_takeover_boss_mode.load(std::memory_order_acquire);
    }

    SelectionSnapshot started{};
    AcquireSRWLockShared(&g_ui_state_lock);
    started = g_last_started_selection;
    ReleaseSRWLockShared(&g_ui_state_lock);
    identity.started_selection_valid = started.valid;
    identity.active_hero_matches_team = identity.started_selection_valid &&
        active_hero_valid &&
        selection_contains_runtime_hero_type(started, active_hero_type_id);

    const auto started_matches = [&](LONG mode) {
        return identity.started_selection_valid &&
            started.hydra == (mode == kTakeoverBossModeHydra);
    };
    const auto set_mode = [&](LONG mode) {
        identity.mode = mode;
        identity.started_mode_matches = started_matches(mode);
    };

    // The controller-selected mode and the exact team used to start this
    // battle are the primary identity. Area, region and kind IDs are omitted
    // deliberately: the game can change them between rotations. Runtime boss
    // objects are still required so a stale takeover cannot target a menu or
    // an unrelated empty encounter.
    if (identity.requested_mode == kTakeoverBossModeChimera) {
        if ((identity.started_selection_valid && started.hydra) ||
            reported_hydra_battle || boss_count == 0) {
            return identity;
        }
        if (identity.chimera_signal ||
            (started_matches(kTakeoverBossModeChimera) &&
             identity.active_hero_matches_team)) {
            set_mode(kTakeoverBossModeChimera);
        }
        return identity;
    }
    if (identity.requested_mode == kTakeoverBossModeHydra) {
        if ((identity.started_selection_valid && !started.hydra) ||
            identity.chimera_signal || boss_count == 0) {
            return identity;
        }
        if (reported_hydra_battle ||
            (started_matches(kTakeoverBossModeHydra) &&
             (identity.active_hero_matches_team || boss_count >= 2))) {
            set_mode(kTakeoverBossModeHydra);
        }
        return identity;
    }

    // Fallback for diagnostic/manual attachment tools that do not yet send an
    // explicit mode. These paths still need a native boss flag or the team
    // captured by the corresponding preparation dialog.
    if (identity.chimera_signal && !reported_hydra_battle && boss_count > 0) {
        set_mode(kTakeoverBossModeChimera);
    } else if (!identity.chimera_signal && boss_count > 0 &&
               (reported_hydra_battle ||
                (started_matches(kTakeoverBossModeHydra) &&
                 (identity.active_hero_matches_team || boss_count >= 2)))) {
        set_mode(kTakeoverBossModeHydra);
    }
    return identity;
}

bool read_model_boss_guard(const QueueCommandRequest& request, void* mode,
                           std::int32_t expected_active_hero_type_id,
                           std::size_t boss_count,
                           std::int32_t& area_id,
                           std::int32_t& region_id) {
    void* processor = nullptr;
    void* context = nullptr;
    void* state = nullptr;
    if (!safe_read(mode, 16, processor) || !processor ||
        !safe_read_field(processor, "<Context>k__BackingField", "Context",
                         context) ||
        !context || !safe_read_field(context, "State", nullptr, state) ||
        !state) {
        diagnostic("command_rejected reason=model_context_unavailable");
        return false;
    }

    Il2CppClass* context_class = nullptr;
    if (!safe_read(context, 0, context_class) || !context_class) {
        diagnostic("command_rejected reason=model_context_class_unavailable");
        return false;
    }
    const MethodInfo* area_method = g_api.class_get_method_from_name(
        context_class, "get_CurrentAreaTypeId", 0);
    const MethodInfo* region_method = g_api.class_get_method_from_name(
        context_class, "get_CurrentRegionTypeId", 0);
    area_id = INT_MIN;
    region_id = INT_MIN;
    if (!runtime_get_int(area_method, context, &area_id)) {
        diagnostic("command_rejected reason=model_area_unavailable");
        return false;
    }
    runtime_get_int(region_method, context, &region_id);

    std::int32_t kind_id = 0;
    std::int32_t round = 0;
    std::int32_t turn = 0;
    std::int32_t player_turn_count = 0;
    bool chimera_preset = false;
    bool hydra_battle = false;
    bool finished = true;
    safe_read_field(state, "_battleKindId", nullptr, kind_id);
    safe_read_field(state, "CurrentRound", nullptr, round);
    safe_read_field(state, "CurrentTurn", nullptr, turn);
    safe_read_field(state, "PlayerTurnCount", nullptr, player_turn_count);
    safe_read_field(state, "IsChimeraPreset", nullptr, chimera_preset);
    safe_read_field(state, "IsHydraBattle", nullptr, hydra_battle);
    safe_read_field(state, "BattleFinished", nullptr, finished);

    void* active_hero = nullptr;
    std::int32_t active_hero_id = 0;
    std::int32_t active_hero_type_id = 0;
    std::int32_t active_hero_turn_count = 0;
    std::int32_t active_hero_form_index = 0;
    bool active_hero_valid = false;
    if (safe_read_field(state, "ActiveHero", nullptr, active_hero) &&
        active_hero) {
        const bool type_read = safe_read_field(
            active_hero, "TypeId", nullptr, active_hero_type_id);
        const bool id_read = safe_read_field(
            active_hero, "<Id>k__BackingField", "Id", active_hero_id);
        safe_read_field(active_hero, "TurnCount", nullptr,
                        active_hero_turn_count);
        safe_read_field(active_hero, "CurrentFormIndex", nullptr,
                        active_hero_form_index);
        active_hero_valid = type_read && id_read && active_hero_id >= 0 &&
            active_hero_type_id > 0;
    }

    Il2CppClass* state_class = nullptr;
    void* chimera = nullptr;
    std::int32_t chimera_id = 0;
    std::int32_t chimera_type_id = 0;
    if (safe_read(state, 0, state_class) && state_class) {
        const MethodInfo* chimera_method =
            g_api.class_get_method_from_name(state_class, "Chimera", 0);
        safe_runtime_invoke_object(chimera_method, state, &chimera);
    }
    if (chimera) {
        safe_read_field(chimera, "<Id>k__BackingField", "Id", chimera_id);
        safe_read_field(chimera, "TypeId", nullptr, chimera_type_id);
    }
    void* target_hero = nullptr;
    std::int32_t model_target_id = 0;
    bool target_hero_valid = false;
    if (state_class) {
        const MethodInfo* find_hero = find_one_int_method(state_class,
                                                          "FindHero");
        safe_runtime_invoke_one_int(find_hero, state, request.target_id,
                                    &target_hero);
    }
    if (target_hero) {
        target_hero_valid = safe_read_field(
            target_hero, "<Id>k__BackingField", "Id", model_target_id) &&
            model_target_id >= 0;
    }

    const AllianceBossIdentity boss_identity = identify_alliance_boss(
        active_hero_valid, active_hero_type_id, chimera_preset, hydra_battle,
        chimera != nullptr && chimera_id >= 0, boss_count);
    const bool active_chimera =
        boss_identity.mode == kTakeoverBossModeChimera;
    const bool active_hydra =
        boss_identity.mode == kTakeoverBossModeHydra;
    const bool expected_area_matches = request.expected_area_id == INT_MIN ||
        request.expected_area_id == area_id;
    const bool expected_region_matches =
        request.expected_region_id == INT_MIN ||
        request.expected_region_id == region_id;
    if ((!active_chimera && !active_hydra) || finished ||
        !expected_area_matches || !expected_region_matches ||
        !active_hero_valid || !target_hero_valid ||
        canonical_runtime_hero_type_id(active_hero_type_id) !=
            canonical_runtime_hero_type_id(expected_active_hero_type_id) ||
        model_target_id != request.target_id ||
        round != request.expected_round ||
        turn != request.expected_turn ||
        player_turn_count != request.expected_player_turn_count ||
        active_hero_id != request.expected_active_hero_id ||
        active_hero_turn_count != request.expected_active_hero_turn_count ||
        active_hero_form_index != request.expected_active_hero_form_index) {
        std::ostringstream message;
        message << "command_rejected reason=model_alliance_boss_guard_failed"
                << " area_id=" << area_id << " region_id=" << region_id
                << " kind_id=" << kind_id
                << " chimera_preset=" << (chimera_preset ? 1 : 0)
                << " hydra_battle=" << (hydra_battle ? 1 : 0)
                << " finished=" << (finished ? 1 : 0)
                << " chimera_id=" << chimera_id
                << " chimera_type_id=" << chimera_type_id
                << " boss_count=" << boss_count
                << " requested_boss_mode="
                << boss_identity.requested_mode
                << " identified_boss_mode=" << boss_identity.mode
                << " started_selection_valid="
                << (boss_identity.started_selection_valid ? 1 : 0)
                << " started_mode_matches="
                << (boss_identity.started_mode_matches ? 1 : 0)
                << " active_hero_matches_team="
                << (boss_identity.active_hero_matches_team ? 1 : 0)
                << " active_hero_id=" << active_hero_id
                << " active_hero_type_id=" << active_hero_type_id
                << " active_hero_turn_count=" << active_hero_turn_count
                << " active_hero_form_index=" << active_hero_form_index
                << " round=" << round << " turn=" << turn
                << " player_turn_count=" << player_turn_count
                << " expected_round=" << request.expected_round
                << " expected_turn=" << request.expected_turn
                << " expected_player_turn_count="
                << request.expected_player_turn_count
                << " expected_active_hero_id="
                << request.expected_active_hero_id
                << " expected_active_hero_turn_count="
                << request.expected_active_hero_turn_count
                << " expected_active_hero_form_index="
                << request.expected_active_hero_form_index
                << " expected_active_hero_type_id="
                << expected_active_hero_type_id
                << " model_target_id=" << model_target_id
                << " target_id=" << request.target_id;
        diagnostic(message.str());
        return false;
    }
    return true;
}

bool read_command_guard(const QueueCommandRequest& request,
                        std::int32_t& area_id, std::int32_t& region_id,
                        bool& chimera_enabled, bool& boss_current) {
    void* context = reinterpret_cast<void*>(request.context);
    void* generator = reinterpret_cast<void*>(request.generator);
    void* mode = reinterpret_cast<void*>(request.mode);
    void* skill_data = reinterpret_cast<void*>(request.skill_data);
    if (!object_has_class_name(generator, "ClientCommandGenerator") ||
        !object_derives_from(mode, "ClientBattleMode") ||
        !object_has_class_name(skill_data, "SkillData")) {
        diagnostic("command_rejected reason=object_class_mismatch");
        return false;
    }

    void* processor = nullptr;
    void* active_generator = nullptr;
    bool waiting = false;
    if (!safe_read(mode, 16, processor) || !processor ||
        !safe_read(mode, 104, active_generator) || active_generator != generator ||
        !safe_read(generator, 56, waiting) || !waiting) {
        diagnostic("command_rejected reason=stale_or_not_waiting");
        return false;
    }

    std::int32_t skill_id = -1;
    std::int32_t skill_type_id = 0;
    std::int32_t cooldown = -1;
    std::int32_t hero_type_id = 0;
    bool passive = true;
    bool blocked = true;
    if (!safe_read(skill_data, 16, skill_id) ||
        !safe_read(skill_data, 24, cooldown) ||
        !safe_read(skill_data, 28, passive) ||
        !safe_read(skill_data, 32, skill_type_id) ||
        !safe_read(skill_data, 36, hero_type_id) ||
        !safe_read(skill_data, 76, blocked) ||
        skill_id != request.skill_id || cooldown != 0 || passive || blocked ||
        skill_type_id != request.verified_skill_type_id) {
        diagnostic("command_rejected reason=skill_guard_changed");
        return false;
    }

    if (!g_original_get_acceptable_targets || !g_acceptable_targets_method) {
        diagnostic("command_rejected reason=acceptable_target_method_missing");
        return false;
    }
    void* acceptable_targets = nullptr;
    void* actors = nullptr;
    void* bosses = nullptr;
    if (!safe_get_acceptable_targets(mode, skill_data, &acceptable_targets) ||
        !safe_read(mode, 112, actors) || !safe_read(mode, 128, bosses)) {
        diagnostic("command_rejected reason=target_collections_unavailable");
        return false;
    }
    const IntDictionaryKeys acceptable_keys =
        read_int_dictionary_keys(acceptable_targets);
    const IntDictionaryKeys actor_keys = read_int_dictionary_keys(actors);
    const IntDictionaryKeys boss_keys = read_int_dictionary_keys(bosses);
    if (!dictionary_contains(acceptable_keys, request.target_id) ||
        (!dictionary_contains(actor_keys, request.target_id) &&
         !dictionary_contains(boss_keys, request.target_id))) {
        diagnostic("command_rejected reason=target_not_acceptable_actor");
        return false;
    }
    diagnostic_actor_names("command_actor_name", actors, skill_type_id);
    diagnostic_actor_names("command_boss_name", bosses, skill_type_id);

    if (!read_model_boss_guard(request, mode, hero_type_id, boss_keys.count,
                               area_id, region_id)) {
        return false;
    }
    boss_current = true;
    chimera_enabled = true;
    const bool has_context =
        context && object_derives_from(context, "ClientBattleViewContext");
    if (!has_context) {
        diagnostic("command_guard_context unavailable_using_model_guard=1");
        std::ostringstream mode_message;
        mode_message << "command_guard nonce=" << request.nonce
                     << " area_id=" << area_id
                     << " region_id=" << region_id
                     << " skill_id=" << skill_id
                     << " skill_type_id=" << skill_type_id
                     << " target_id=" << request.target_id
                     << " acceptable_boss=1 waiting=1";
        diagnostic(mode_message.str());
        return true;
    }
    std::int32_t view_area_id = INT_MIN;
    std::int32_t view_region_id = INT_MIN;
    bool view_chimera_enabled = false;
    bool view_boss_current = false;
    const bool view_area_read = g_area_type_method &&
        runtime_get_int(g_area_type_method, context, &view_area_id);
    const bool view_region_read = g_region_type_method &&
        runtime_get_int(g_region_type_method, context, &view_region_id);
    const bool view_chimera_read = g_chimera_enabled_method &&
        runtime_get_bool(g_chimera_enabled_method, context,
                         &view_chimera_enabled);
    const bool view_boss_read = g_enemy_boss_current_method &&
        runtime_get_bool(g_enemy_boss_current_method, context,
                         &view_boss_current);
    std::ostringstream message;
    message << "command_view_guard_informational nonce=" << request.nonce
            << " model_area_id=" << area_id
            << " model_region_id=" << region_id
            << " view_area_read=" << (view_area_read ? 1 : 0)
            << " view_area_id=" << view_area_id
            << " view_region_read=" << (view_region_read ? 1 : 0)
            << " view_region_id=" << view_region_id
            << " view_chimera_read=" << (view_chimera_read ? 1 : 0)
            << " view_chimera_enabled=" << (view_chimera_enabled ? 1 : 0)
            << " view_boss_read=" << (view_boss_read ? 1 : 0)
            << " view_boss_current=" << (view_boss_current ? 1 : 0)
            << " authoritative_model_and_target_guard=1";
    diagnostic(message.str());
    return true;
}

bool validate_free_regroup_model(void* mode) {
    void* processor = nullptr;
    void* context = nullptr;
    void* state = nullptr;
    if (!mode || !object_derives_from(mode, "ClientBattleMode") ||
        !safe_read(mode, 16, processor) || !processor ||
        !safe_read_field(processor, "<Context>k__BackingField", "Context",
                         context) ||
        !context || !safe_read_field(context, "State", nullptr, state) ||
        !state) {
        return false;
    }
    Il2CppClass* context_class = nullptr;
    if (!safe_read(context, 0, context_class) || !context_class) {
        return false;
    }
    bool chimera_preset = false;
    bool finished = true;
    safe_read_field(state, "IsChimeraPreset", nullptr, chimera_preset);
    safe_read_field(state, "BattleFinished", nullptr, finished);
    Il2CppClass* state_class = nullptr;
    void* chimera = nullptr;
    std::int32_t chimera_id = -1;
    std::int32_t chimera_type_id = 0;
    if (safe_read(state, 0, state_class) && state_class) {
        safe_runtime_invoke_object(g_api.class_get_method_from_name(
                                       state_class, "Chimera", 0),
                                   state, &chimera);
    }
    if (chimera) {
        safe_read_field(chimera, "<Id>k__BackingField", "Id", chimera_id);
        safe_read_field(chimera, "TypeId", nullptr, chimera_type_id);
    }
    SelectionSnapshot started{};
    AcquireSRWLockShared(&g_ui_state_lock);
    started = g_last_started_selection;
    ReleaseSRWLockShared(&g_ui_state_lock);
    const LONG requested_mode =
        g_takeover_boss_mode.load(std::memory_order_acquire);
    const bool chimera_session =
        requested_mode == kTakeoverBossModeChimera ||
        (requested_mode == kTakeoverBossModeUnknown && started.valid &&
         !started.hydra);
    return chimera_session && !finished && chimera && chimera_id >= 0 &&
        (chimera_preset || is_chimera_type_id(chimera_type_id));
}

bool disable_selection_auto_battle(void* context) {
    void* checkbox = nullptr;
    void* selected_property = nullptr;
    Il2CppClass* property_class = nullptr;
    const bool checkbox_read = context && (
        safe_read_field(context, "_autoBattleCheckbox", nullptr, checkbox) ||
        safe_read(context, 384, checkbox));
    if (!checkbox_read || !checkbox ||
        !safe_read(checkbox, 80, selected_property) || !selected_property ||
        !safe_read(selected_property, 0, property_class) || !property_class) {
        diagnostic("selection_auto_disable_missing_property");
        return false;
    }
    const MethodInfo* setter = nullptr;
    bool setter_uses_two_parameters = false;
    std::ostringstream property_methods;
    property_methods << "selection_auto_property_methods";
    Il2CppClass* search_class = property_class;
    for (int depth = 0; search_class && depth < 16 && !setter; ++depth) {
        const char* class_name = g_api.class_get_name(search_class);
        property_methods << " class="
                         << (class_name ? class_name : "<unknown>") << '[';
        void* iterator = nullptr;
        while (const MethodInfo* method =
                   g_api.class_get_methods(search_class, &iterator)) {
            const char* name = g_api.method_get_name(method);
            if (name && (std::strstr(name, "Value") ||
                         std::strstr(name, "value") ||
                         std::strstr(name, "Set") ||
                         std::strstr(name, "set") ||
                         std::strstr(name, "Change") ||
                         std::strstr(name, "Notify"))) {
                property_methods << name << '/'
                                 << g_api.method_get_param_count(method) << ',';
            }
            if (name && g_api.method_get_param_count(method) == 1 &&
                (std::strcmp(name, "set_Value") == 0 ||
                 std::strcmp(name, "SetValue") == 0 ||
                 std::strcmp(name, "set_value") == 0)) {
                setter = method;
                break;
            }
            if (name && std::strcmp(name, "Set") == 0 &&
                g_api.method_get_param_count(method) == 2) {
                setter = method;
                setter_uses_two_parameters = true;
                break;
            }
        }
        property_methods << ']';
        search_class = g_api.class_get_parent(search_class);
    }
    if (!setter) {
        diagnostic(property_methods.str());
        return false;
    }
    const bool updated = setter_uses_two_parameters
        ? safe_runtime_invoke_two_bool_void(setter, selected_property, false,
                                            false)
        : safe_runtime_invoke_one_bool_void(setter, selected_property, false);
    if (!updated) {
        return false;
    }
    diagnostic("selection_auto_disabled");
    return true;
}

bool claim_turn_execution(const QueueCommandRequest& request) {
    const ExecutedTurnToken proposed{
        request.mode,
        request.expected_round,
        request.expected_turn,
        request.expected_player_turn_count,
        request.expected_active_hero_id,
        request.expected_active_hero_turn_count,
        request.expected_active_hero_form_index};
    AcquireSRWLockExclusive(&g_executed_turn_lock);
    const bool duplicate = g_executed_turn_valid &&
        g_executed_turn.mode == proposed.mode &&
        g_executed_turn.round == proposed.round &&
        g_executed_turn.turn == proposed.turn &&
        g_executed_turn.player_turn_count == proposed.player_turn_count &&
        g_executed_turn.active_hero_id == proposed.active_hero_id &&
        g_executed_turn.active_hero_turn_count ==
            proposed.active_hero_turn_count &&
        g_executed_turn.active_hero_form_index ==
            proposed.active_hero_form_index;
    if (!duplicate) {
        g_executed_turn = proposed;
        g_executed_turn_valid = true;
    }
    ReleaseSRWLockExclusive(&g_executed_turn_lock);
    return !duplicate;
}

void clear_executed_turn() {
    AcquireSRWLockExclusive(&g_executed_turn_lock);
    g_executed_turn = {};
    g_executed_turn_valid = false;
    ReleaseSRWLockExclusive(&g_executed_turn_lock);
}

void drain_pending_command() {
    QueueCommandRequest request{};
    AcquireSRWLockExclusive(&g_command_lock);
    if (!g_command_pending.exchange(false, std::memory_order_acq_rel)) {
        ReleaseSRWLockExclusive(&g_command_lock);
        return;
    }
    request = g_pending_command;
    ReleaseSRWLockExclusive(&g_command_lock);

    if (g_takeover_state.load(std::memory_order_acquire) != 1 ||
        g_takeover_session.load(std::memory_order_acquire) !=
            request.session_id) {
        diagnostic("command_rejected reason=takeover_not_active nonce=" +
                   std::to_string(request.nonce));
        publish_ack(request, "rejected", "takeover_not_active");
        return;
    }
    if (!validate_takeover_account()) {
        publish_ack(request, "rejected", "account_changed");
        return;
    }

    std::int32_t area_id = 0;
    std::int32_t region_id = 0;
    bool chimera_enabled = false;
    bool boss_current = false;
    if (!read_command_guard(request, area_id, region_id, chimera_enabled,
                            boss_current)) {
        publish_ack(request, "rejected", "guard_failed");
        return;
    }

    if (!(request.flags & kCommandFlagExecute)) {
        diagnostic("command_probe_complete nonce=" + std::to_string(request.nonce));
        publish_ack(request, "validated");
        return;
    }
    if ((request.expected_area_id != INT_MIN &&
         request.expected_area_id != area_id) ||
        (request.expected_region_id != INT_MIN &&
         request.expected_region_id != region_id)) {
        diagnostic("command_rejected reason=battle_guard_changed nonce=" +
                   std::to_string(request.nonce));
        publish_ack(request, "rejected", "battle_guard_changed");
        return;
    }
    if (request.target_id < 0 || request.skill_id < 0 ||
        request.skill_id > 20 || !g_original_create_manual_command ||
        !g_create_manual_method) {
        diagnostic("command_rejected reason=invalid_action nonce=" +
                   std::to_string(request.nonce));
        publish_ack(request, "rejected", "invalid_action");
        return;
    }
    if (!claim_turn_execution(request)) {
        diagnostic("command_rejected reason=duplicate_turn nonce=" +
                   std::to_string(request.nonce));
        publish_ack(request, "rejected", "duplicate_turn");
        return;
    }

    void* generator = reinterpret_cast<void*>(request.generator);
    diagnostic("command_execute nonce=" + std::to_string(request.nonce) +
               " target_id=" + std::to_string(request.target_id) +
               " skill_id=" + std::to_string(request.skill_id) +
               " main_thread=" + std::to_string(GetCurrentThreadId()));
    bool waiting_after = false;
    if (safe_call_create_manual(generator, request.target_id, request.skill_id,
                                &waiting_after)) {
        diagnostic("command_submitted nonce=" +
                   std::to_string(request.nonce) + " waiting_after=" +
                   std::to_string(waiting_after ? 1 : 0));
        publish_ack(request, "submitted");
    } else {
        diagnostic("command_execute_exception nonce=" +
                   std::to_string(request.nonce));
        publish_ack(request, "exception", "create_manual_exception");
    }
}

void drain_pending_lifecycle_command() {
    LifecycleCommandRequest request{};
    AcquireSRWLockExclusive(&g_lifecycle_command_lock);
    if (!g_lifecycle_command_pending.exchange(false,
                                               std::memory_order_acq_rel)) {
        ReleaseSRWLockExclusive(&g_lifecycle_command_lock);
        return;
    }
    request = g_pending_lifecycle_command;
    ReleaseSRWLockExclusive(&g_lifecycle_command_lock);

    if (g_takeover_state.load(std::memory_order_acquire) != 1 ||
        g_takeover_session.load(std::memory_order_acquire) !=
            request.session_id) {
        publish_lifecycle_ack(request, "rejected", "takeover_not_active");
        return;
    }
    if (!validate_takeover_account()) {
        publish_lifecycle_ack(request, "rejected", "account_changed");
        return;
    }
    if (request.action == kLifecycleRestartHydraResult) {
        void* context = g_result_context.load(std::memory_order_acquire);
        if (!context || context != reinterpret_cast<void*>(request.context) ||
            g_screen_state.load(std::memory_order_acquire) != kScreenResult ||
            g_takeover_boss_mode.load(std::memory_order_acquire) !=
                kTakeoverBossModeHydra ||
            !object_has_class_name(
                context, "BattleFinishAllianceHydraDialogContext") ||
            !g_hydra_result_restart_pressed_method) {
            publish_lifecycle_ack(request, "rejected",
                                  "hydra_result_context_changed");
            return;
        }
        diagnostic("lifecycle_hydra_result_regroup_pressed nonce=" +
                   std::to_string(request.nonce));
        g_internal_action_depth.fetch_add(1, std::memory_order_acq_rel);
        const bool invoked = safe_runtime_invoke_void(
            g_hydra_result_restart_pressed_method, context);
        g_internal_action_depth.fetch_sub(1, std::memory_order_acq_rel);
        if (!invoked) {
            publish_lifecycle_ack(request, "exception",
                                  "hydra_result_restart_exception");
            return;
        }
        publish_lifecycle_ack(request, "submitted",
                              "hydra_result_restart_submitted");
        return;
    }
    if (request.action == kLifecycleFreeRegroup ||
        request.action == kLifecyclePrepareFreeRegroup) {
        void* context = g_battle_context.load(std::memory_order_acquire);
        void* mode = g_battle_mode.load(std::memory_order_acquire);
        if (!context || context != reinterpret_cast<void*>(request.context) ||
            g_screen_state.load(std::memory_order_acquire) != kScreenBattle ||
            !object_has_class_name(context, "ClientBattleViewContext") ||
            !mode || !g_execute_cancel_chimera_method ||
            !g_cancel_chimera_method) {
            publish_lifecycle_ack(request, "rejected",
                                  "battle_context_changed");
            return;
        }
        if (!validate_free_regroup_model(mode)) {
            publish_lifecycle_ack(request, "rejected",
                                  "free_regroup_guard_failed");
            return;
        }
        if (request.action == kLifecyclePrepareFreeRegroup) {
            diagnostic("lifecycle_free_regroup_prepare nonce=" +
                       std::to_string(request.nonce));
            g_internal_action_depth.fetch_add(1, std::memory_order_acq_rel);
            const bool invoked =
                safe_runtime_invoke_void(g_cancel_chimera_method, context);
            g_internal_action_depth.fetch_sub(1, std::memory_order_acq_rel);
            if (!invoked) {
                publish_lifecycle_ack(request, "exception",
                                      "free_regroup_prepare_exception");
                return;
            }
            publish_lifecycle_ack(request, "submitted",
                                  "free_regroup_validation_started");
            return;
        }
        void* validation_response = nullptr;
        void* validation_command = nullptr;
        bool validation_in_progress = false;
        safe_read(context, 280, validation_response);
        safe_read(context, 296, validation_command);
        safe_read(context, 316, validation_in_progress);
        diagnostic("lifecycle_free_regroup_ready_check response=" +
                   std::to_string(reinterpret_cast<std::uintptr_t>(
                       validation_response)) +
                   " command=" +
                   std::to_string(reinterpret_cast<std::uintptr_t>(
                       validation_command)) +
                   " in_progress=" +
                   std::to_string(validation_in_progress ? 1 : 0));
        if (!validation_response || validation_in_progress) {
            publish_lifecycle_ack(request, "rejected",
                                  "free_regroup_not_prepared");
            return;
        }
        diagnostic("lifecycle_free_regroup_execute nonce=" +
                   std::to_string(request.nonce));
        g_internal_action_depth.fetch_add(1, std::memory_order_acq_rel);
        const bool invoked = safe_runtime_invoke_void(
            g_execute_cancel_chimera_method, context);
        g_internal_action_depth.fetch_sub(1, std::memory_order_acq_rel);
        if (!invoked) {
            publish_lifecycle_ack(request, "exception",
                                  "free_regroup_exception");
            return;
        }
        publish_lifecycle_ack(request, "submitted",
                              "free_regroup_stop_no_restart");
        return;
    }
    if (request.action != kLifecycleStartBattle &&
        request.action != kLifecycleRefreshTeamSelection &&
        request.action != kLifecycleSelectHeroes) {
        publish_lifecycle_ack(request, "rejected", "unsupported_action");
        return;
    }
    void* context = g_selection_context.load(std::memory_order_acquire);
    if (!context || context != reinterpret_cast<void*>(request.context) ||
        g_screen_state.load(std::memory_order_acquire) !=
            kScreenTeamSelection ||
        (!object_has_class_name(context,
                                "HeroesSelectionChimeraDialogContext") &&
         !object_has_class_name(context,
                                "HeroesSelectionHydraDialogContext"))) {
        publish_lifecycle_ack(request, "rejected",
                              "team_selection_context_changed");
        return;
    }

    capture_selection_state(context, "team_selection_revalidated");
    SelectionSnapshot selection{};
    AcquireSRWLockShared(&g_ui_state_lock);
    selection = g_selection_snapshot;
    ReleaseSRWLockShared(&g_ui_state_lock);
    if (request.action == kLifecycleRefreshTeamSelection) {
        publish_lifecycle_ack(request, "validated",
                              "team_selection_refreshed");
        return;
    }
    if (request.action == kLifecycleSelectHeroes) {
        if (request.hero_count != request.hero_ids.size() ||
            selection.hydra || !g_selection_hero_picked_method ||
            !selection.valid || selection.quick_battle) {
            publish_lifecycle_ack(request, "rejected",
                                  "hero_selection_guard_failed");
            return;
        }
        for (std::size_t index = 0; index < request.hero_count; ++index) {
            if (request.hero_ids[index] <= 0) {
                publish_lifecycle_ack(request, "rejected",
                                      "invalid_hero_selection");
                return;
            }
            for (std::size_t previous = 0; previous < index; ++previous) {
                if (request.hero_ids[index] == request.hero_ids[previous]) {
                    publish_lifecycle_ack(request, "rejected",
                                          "duplicate_hero_selection");
                    return;
                }
            }
        }
        if (selection.hero_count > 0) {
            bool same = selection.hero_count == request.hero_count;
            for (std::size_t index = 0;
                 same && index < request.hero_count; ++index) {
                same = selection.hero_ids[index] == request.hero_ids[index];
            }
            publish_lifecycle_ack(
                request, same ? "validated" : "rejected",
                same ? "hero_selection_already_matches"
                     : "hero_selection_not_empty");
            return;
        }

        diagnostic("lifecycle_select_heroes_execute nonce=" +
                   std::to_string(request.nonce));
        g_internal_action_depth.fetch_add(1, std::memory_order_acq_rel);
        bool invoked = true;
        for (std::size_t index = 0; index < request.hero_count; ++index) {
            if (!safe_runtime_invoke_int_bool_void(
                    g_selection_hero_picked_method, context,
                    request.hero_ids[index], true)) {
                invoked = false;
                break;
            }
        }
        g_internal_action_depth.fetch_sub(1, std::memory_order_acq_rel);
        if (!invoked) {
            capture_selection_state(context, "hero_selection_exception");
            publish_lifecycle_ack(request, "exception",
                                  "hero_selection_exception");
            return;
        }
        capture_selection_state(context, "heroes_selected_by_controller");
        AcquireSRWLockShared(&g_ui_state_lock);
        selection = g_selection_snapshot;
        ReleaseSRWLockShared(&g_ui_state_lock);
        bool matches = selection.valid && selection.filled &&
                       selection.hero_count == request.hero_count;
        for (std::size_t index = 0;
             matches && index < request.hero_count; ++index) {
            matches = selection.hero_ids[index] == request.hero_ids[index];
        }
        if (!matches) {
            publish_lifecycle_ack(request, "rejected",
                                  "hero_selection_verification_failed");
            return;
        }
        publish_lifecycle_ack(request, "submitted", "heroes_selected");
        return;
    }
    if (selection.auto_battle) {
        g_internal_action_depth.fetch_add(1, std::memory_order_acq_rel);
        const bool disabled = disable_selection_auto_battle(context);
        g_internal_action_depth.fetch_sub(1, std::memory_order_acq_rel);
        if (!disabled) {
            publish_lifecycle_ack(request, "rejected",
                                  "auto_battle_disable_failed");
            return;
        }
        capture_selection_state(context,
                                "team_selection_auto_battle_disabled");
        AcquireSRWLockShared(&g_ui_state_lock);
        selection = g_selection_snapshot;
        ReleaseSRWLockShared(&g_ui_state_lock);
        if (selection.auto_battle) {
            // The observable checkbox value can settle on the next UI tick,
            // especially in the Hydra dialog.  Tell the controller to retry
            // instead of reporting a generic guard failure or pretending the
            // battle was started.
            publish_lifecycle_ack(request, "rejected",
                                  "auto_battle_disable_pending");
            return;
        }
    }
    const MethodInfo* start_battle_click_method = selection.hydra
        ? g_hydra_selection_start_battle_click_method
        : g_selection_start_battle_click_method;
    if (!selection.valid || selection.auto_battle || selection.quick_battle ||
        !start_battle_click_method) {
        publish_lifecycle_ack(request, "rejected",
                              "team_selection_guard_failed");
        return;
    }

    diagnostic("lifecycle_start_battle_execute nonce=" +
               std::to_string(request.nonce));
    g_internal_action_depth.fetch_add(1, std::memory_order_acq_rel);
    const bool invoked = safe_runtime_invoke_void(
        start_battle_click_method, context);
    g_internal_action_depth.fetch_sub(1, std::memory_order_acq_rel);
    if (!invoked) {
        publish_lifecycle_ack(request, "exception",
                              "start_battle_exception");
        return;
    }
    publish_lifecycle_ack(request, "submitted");
    publish_lifecycle("start_battle_submitted");
}

LRESULT CALLBACK agent_window_proc(HWND window, UINT message, WPARAM wparam,
                                   LPARAM lparam) {
    if (message == g_command_message) {
        void* attached_here = nullptr;
        if (!g_api.thread_current()) {
            attached_here = g_api.thread_attach(g_api.domain_get());
        }
        drain_pending_lifecycle_command();
        drain_pending_command();
        if (attached_here) {
            g_api.thread_detach(attached_here);
        }
        return 0;
    }
    if (message == WM_TIMER && wparam == kDecisionCaptureTimerId) {
        KillTimer(window, kDecisionCaptureTimerId);
        void* generator = g_command_generator.load(std::memory_order_acquire);
        if (generator) {
            capture_decision_state(generator);
        }
        return 0;
    }
    if (message == WM_TIMER && wparam == kSelectionCaptureTimerId) {
        void* context = g_selection_context.load(std::memory_order_acquire);
        if (context && g_screen_state.load(std::memory_order_acquire) ==
                           kScreenTeamSelection) {
            capture_selection_state(
                context, "team_selection_changed", nullptr, false);
        }
        else {
            KillTimer(window, kSelectionCaptureTimerId);
        }
        return 0;
    }
    if (message == WM_TIMER && wparam == kAccountRefreshTimerId) {
        log_account_identity(refresh_app_model_instance());
        return 0;
    }
    return CallWindowProcW(g_original_window_proc, window, message, wparam, lparam);
}

BOOL CALLBACK find_game_window(HWND window, LPARAM parameter) {
    DWORD pid = 0;
    const DWORD thread_id = GetWindowThreadProcessId(window, &pid);
    if (pid != GetCurrentProcessId() || !IsWindowVisible(window) ||
        GetWindow(window, GW_OWNER)) {
        return TRUE;
    }
    auto* result = reinterpret_cast<HWND*>(parameter);
    *result = window;
    g_window_thread_id = thread_id;
    return FALSE;
}

bool install_window_dispatch() {
    g_command_message =
        RegisterWindowMessageW(L"RaidChimeraAgent.Command.v1");
    HWND window = nullptr;
    if (g_command_message) {
        EnumWindows(&find_game_window, reinterpret_cast<LPARAM>(&window));
    }
    if (!g_command_message || !window) {
        diagnostic("window_dispatch_find_failed error=" +
                   std::to_string(GetLastError()));
        return false;
    }
    SetLastError(ERROR_SUCCESS);
    const LONG_PTR previous = SetWindowLongPtrW(
        window, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&agent_window_proc));
    if (!previous && GetLastError() != ERROR_SUCCESS) {
        diagnostic("window_dispatch_install_failed error=" +
                   std::to_string(GetLastError()));
        return false;
    }
    g_game_window = window;
    g_original_window_proc = reinterpret_cast<WNDPROC>(previous);
    g_window_dispatch_installed.store(true, std::memory_order_release);
    if (!SetTimer(g_game_window, kAccountRefreshTimerId,
                  kAccountRefreshIntervalMs, nullptr)) {
        diagnostic("account_refresh_timer_failed error=" +
                   std::to_string(GetLastError()));
    }
    diagnostic("window_dispatch_installed window=" +
               std::to_string(reinterpret_cast<std::uintptr_t>(window)) +
               " thread=" + std::to_string(g_window_thread_id));
    return true;
}

bool remove_window_dispatch() {
    g_command_pending.store(false, std::memory_order_release);
    g_lifecycle_command_pending.store(false, std::memory_order_release);
    if (g_game_window) {
        KillTimer(g_game_window, kDecisionCaptureTimerId);
        KillTimer(g_game_window, kSelectionCaptureTimerId);
        KillTimer(g_game_window, kAccountRefreshTimerId);
    }
    if (!g_window_dispatch_installed.exchange(false,
                                               std::memory_order_acq_rel)) {
        return true;
    }
    if (!IsWindow(g_game_window)) {
        g_game_window = nullptr;
        g_original_window_proc = nullptr;
        return true;
    }
    SetLastError(ERROR_SUCCESS);
    const LONG_PTR previous = SetWindowLongPtrW(
        g_game_window, GWLP_WNDPROC,
        reinterpret_cast<LONG_PTR>(g_original_window_proc));
    const DWORD error = GetLastError();
    if (!previous && error != ERROR_SUCCESS) {
        g_window_dispatch_installed.store(true, std::memory_order_release);
        diagnostic("window_dispatch_remove_failed error=" +
                   std::to_string(error));
        return false;
    }
    diagnostic("window_dispatch_removed");
    g_game_window = nullptr;
    g_original_window_proc = nullptr;
    return true;
}

void capture_battle_context(void* context) {
    void* previous_context =
        g_battle_context.exchange(context, std::memory_order_acq_rel);
    if (previous_context != context) {
        g_last_chimera_damage.store(0, std::memory_order_release);
        g_last_chimera_competition_points.store(0,
                                                std::memory_order_release);
        reset_hydra_damage_tracker(context);
    }
    void* mode = nullptr;
    void* processor = nullptr;
    void* generator = nullptr;
    safe_read(context, 176, mode);
    safe_read(mode, 16, processor);
    safe_read(mode, 104, generator);
    g_battle_processor.store(processor, std::memory_order_release);
    g_command_generator.store(generator, std::memory_order_release);
    g_battle_mode.store(mode, std::memory_order_release);

    bool waiting = false;
    safe_read(generator, 56, waiting);
    Il2CppClass* context_class = nullptr;
    const char* context_class_name = "";
    if (safe_read(context, 0, context_class) && context_class) {
        context_class_name = g_api.class_get_name(context_class);
    }
    std::ostringstream message;
    message << "battle_context_captured context=" << context
            << " class=" << (context_class_name ? context_class_name : "")
            << " mode=" << mode
            << " processor=" << processor << " generator=" << generator
            << " waiting=" << waiting;
    diagnostic(message.str());
}

void capture_generator_state(const char* event, void* generator) {
    g_command_generator.store(generator, std::memory_order_release);

    std::int32_t mode_type = 0;
    std::uint64_t hud_state = 0;
    void* turn_action_generator = nullptr;
    bool waiting = false;
    std::int64_t user_id = 0;
    std::uint64_t selected_skill_raw = 0;
    safe_read(generator, 24, mode_type);
    safe_read(generator, 32, hud_state);
    safe_read(generator, 40, turn_action_generator);
    safe_read(generator, 56, waiting);
    safe_read(generator, 64, user_id);
    safe_read(generator, 80, selected_skill_raw);

    std::ostringstream message;
    message << event << " generator=" << generator
            << " mode_type=" << mode_type
            << " hud_state_raw=" << hud_state
            << " turn_action_generator=" << turn_action_generator
            << " waiting=" << (waiting ? 1 : 0)
            << " user_id=" << user_id
            << " selected_skill_raw=" << selected_skill_raw;
    diagnostic(message.str());
}

void capture_mode_state(const char* event, void* mode) {
    void* processor = nullptr;
    void* generator = nullptr;
    safe_read(mode, 16, processor);
    safe_read(mode, 104, generator);
    g_battle_processor.store(processor, std::memory_order_release);
    g_command_generator.store(generator, std::memory_order_release);
    g_battle_mode.store(mode, std::memory_order_release);

    std::ostringstream message;
    message << event << " mode=" << mode << " processor=" << processor
            << " generator=" << generator;
    diagnostic(message.str());
}

void capture_skill_data(const char* event, void* skill_data) {
    std::int32_t id = 0;
    std::int32_t level = 0;
    std::int32_t cooldown = 0;
    bool passive = false;
    bool selected = false;
    std::int32_t type_id = 0;
    std::int32_t hero_type_id = 0;
    void* name_object = nullptr;
    void* description_object = nullptr;
    void* icon_object = nullptr;
    std::int32_t default_cooldown = 0;
    std::uint64_t targets_raw = 0;
    safe_read(skill_data, 16, id);
    safe_read(skill_data, 20, level);
    safe_read(skill_data, 24, cooldown);
    safe_read(skill_data, 28, passive);
    safe_read(skill_data, 29, selected);
    safe_read(skill_data, 32, type_id);
    safe_read(skill_data, 36, hero_type_id);
    safe_read(skill_data, 40, name_object);
    safe_read(skill_data, 48, description_object);
    safe_read(skill_data, 56, icon_object);
    safe_read(skill_data, 64, default_cooldown);
    safe_read(skill_data, 68, targets_raw);
    const std::string name = il2cpp_string_utf8(name_object);
    const std::string description = il2cpp_string_utf8(description_object);
    const std::string icon = il2cpp_string_utf8(icon_object);
    const ResolvedText hero_name = resolve_static_hero_name(hero_type_id);
    const ResolvedText skill_name = resolve_static_skill_name(type_id);

    std::ostringstream message;
    message << event << " skill_data=" << skill_data << " id=" << id
            << " level=" << level << " cooldown=" << cooldown
            << " passive=" << (passive ? 1 : 0)
            << " selected=" << (selected ? 1 : 0)
            << " type_id=" << type_id << " hero_type_id=" << hero_type_id
            << " name=\"" << name << "\""
            << " description_length=" << description.size()
            << " icon=\"" << icon << "\""
            << " hero_name_key=\"" << hero_name.key << "\""
            << " hero_name=\"" << hero_name.display << "\""
            << " skill_name_key=\"" << skill_name.key << "\""
            << " skill_name=\"" << skill_name.display << "\""
            << " default_cooldown=" << default_cooldown
            << " targets_raw=" << targets_raw;
    diagnostic(message.str());
}

std::int32_t actor_hero_type_id(void* actor) {
    Il2CppClass* klass = nullptr;
    std::size_t offset = 0;
    std::int32_t type_id = 0;
    if (safe_read(actor, 0, klass) && klass &&
        find_field_offset(klass, "HeroTypeId",
                          "<HeroTypeId>k__BackingField", offset)) {
        safe_read(actor, offset, type_id);
    }
    return type_id;
}

void* find_battle_hero(void* mode, std::int32_t actor_id);

void append_model_skills(std::ostringstream& output, void* hero) {
    void* skills_list = nullptr;
    ObjectListItems skills{};
    if (safe_read_field(hero, "_heroSkills", nullptr, skills_list) &&
        skills_list) {
        skills = read_object_list(skills_list);
    }
    if (!skills.count) {
        skills_list = nullptr;
        if (safe_read_field(hero, "Skills", nullptr, skills_list) &&
            skills_list) {
            skills = read_object_list(skills_list);
        }
    }
    output << '[';
    bool first = true;
    for (std::size_t index = 0; index < skills.count; ++index) {
        void* skill = skills.objects[index];
        std::int32_t type_id = 0;
        std::int32_t cooldown = 0;
        std::int32_t max_cooldown = 0;
        std::int32_t level = 0;
        bool disabled = false;
        bool is_hero_skill = false;
        bool hidden_on_hud = false;
        bool active_skill = false;
        safe_read_field(skill, "TypeId", nullptr, type_id);
        safe_read_field(skill, "Cooldown", nullptr, cooldown);
        safe_read_field(skill, "MaxCooldown", nullptr, max_cooldown);
        safe_read_field(skill, "Level", nullptr, level);
        safe_read_field(skill, "<Disabled>k__BackingField", "Disabled",
                        disabled);
        safe_read_field(skill, "<IsHeroSkill>k__BackingField", "IsHeroSkill",
                        is_hero_skill);
        Il2CppClass* skill_class = nullptr;
        if (safe_read(skill, 0, skill_class) && skill_class) {
            const MethodInfo* hidden_method = g_api.class_get_method_from_name(
                skill_class, "IsHiddenOnHud", 0);
            const MethodInfo* active_method = g_api.class_get_method_from_name(
                skill_class, "get_IsActive", 0);
            runtime_get_bool(hidden_method, skill, &hidden_on_hud);
            runtime_get_bool(active_method, skill, &active_skill);
        }
        if (!is_hero_skill) {
            continue;
        }
        if (!first) {
            output << ',';
        }
        first = false;
        const ResolvedText name = resolve_static_skill_name(type_id);
        output << "{\"typeId\":" << type_id
               << ",\"nameKey\":\"" << json_escape(name.key)
               << "\",\"name\":\"" << json_escape(name.display)
               << "\",\"level\":" << level
               << ",\"cooldown\":" << cooldown
               << ",\"maxCooldown\":" << max_cooldown
               << ",\"ready\":"
               << ((!disabled && active_skill && !hidden_on_hud &&
                    cooldown == 0)
                       ? "true" : "false")
               << ",\"disabled\":" << (disabled ? "true" : "false")
               << ",\"heroSkill\":"
               << (is_hero_skill ? "true" : "false")
               << ",\"activeSkill\":"
               << (active_skill ? "true" : "false")
               << ",\"hiddenOnHud\":"
               << (hidden_on_hud ? "true" : "false")
               << '}';
    }
    output << ']';
}

bool append_static_skill_json(std::ostringstream& output, void* skill_type,
                              std::int32_t slot,
                              std::int32_t form_index,
                              bool& first_skill) {
    Il2CppClass* skill_class = nullptr;
    std::int32_t type_id = 0;
    std::int32_t cooldown = 0;
    bool active = false;
    bool hidden = false;
    if (!safe_read(skill_type, 0, skill_class) || !skill_class ||
        !safe_read_field(skill_type, "Id", nullptr, type_id)) {
        return false;
    }
    safe_read_field(skill_type, "Cooldown", nullptr, cooldown);
    runtime_get_bool(g_api.class_get_method_from_name(
                         skill_class, "get_IsActive", 0),
                     skill_type, &active);
    runtime_get_bool(g_api.class_get_method_from_name(
                         skill_class, "get_IsHiddenOnHud", 0),
                     skill_type, &hidden);
    if (!active || hidden || type_id <= 0) {
        return false;
    }
    const ResolvedText skill_name = resolve_object_name(skill_type);
    const ResolvedText description = resolve_object_description(skill_type);
    if (!first_skill) {
        output << ',';
    }
    first_skill = false;
    output << "{\"slot\":" << slot
           << ",\"formIndex\":" << form_index
           << ",\"typeId\":" << type_id
           << ",\"nameKey\":\"" << json_escape(skill_name.key)
           << "\",\"name\":\"" << json_escape(skill_name.display)
           << "\",\"descriptionKey\":\""
           << json_escape(description.key)
           << "\",\"description\":\""
           << json_escape(description.display)
           << "\",\"defaultCooldown\":" << cooldown
           << ",\"activeSkill\":true,\"hiddenOnHud\":false}";
    return true;
}

void* find_skill_type_in_snapshot(const ObjectListItems& skills,
                                  std::int32_t wanted_type_id) {
    for (std::size_t index = 0; index < skills.count; ++index) {
        std::int32_t type_id = 0;
        if (safe_read_field(skills.objects[index], "Id", nullptr, type_id) &&
            type_id == wanted_type_id) {
            return skills.objects[index];
        }
    }
    return nullptr;
}

bool append_static_hero_json(std::ostringstream& output, void* hero_type,
                             std::int32_t hero_id, bool& first_hero) {
    Il2CppClass* hero_class = nullptr;
    if (hero_id <= 0 || !safe_read(hero_type, 0, hero_class) || !hero_class) {
        return false;
    }
    const ResolvedText hero_name = resolve_object_name(hero_type);
    const std::string avatar = resolve_hero_avatar(hero_type, 0);
    bool is_metamorph = false;
    runtime_get_bool(g_api.class_get_method_from_name(
                         hero_class, "get_IsMetamorph", 0),
                     hero_type, &is_metamorph);

    void* skills_list = nullptr;
    ObjectListItems skill_types{};
    const MethodInfo* all_skills_method =
        g_api.class_get_method_from_name(hero_class, "get_AllSkillTypes", 0);
    if (safe_runtime_invoke_object(all_skills_method, hero_type,
                                   &skills_list)) {
        skill_types = read_object_list(skills_list);
    }
    void* additional_skills_list = nullptr;
    ObjectListItems additional_skill_types{};
    const MethodInfo* all_additional_skills_method =
        g_api.class_get_method_from_name(hero_class,
                                         "get_AllAdditionalSkillTypes", 0);
    if (safe_runtime_invoke_object(all_additional_skills_method, hero_type,
                                   &additional_skills_list)) {
        additional_skill_types = read_object_list(additional_skills_list);
    }

    if (!first_hero) {
        output << ',';
    }
    first_hero = false;
    output << "{\"typeId\":" << hero_id
           << ",\"nameKey\":\"" << json_escape(hero_name.key)
           << "\",\"name\":\"" << json_escape(hero_name.display)
           << "\",\"avatar\":\"" << json_escape(avatar)
           << "\",\"isMetamorph\":"
           << (is_metamorph ? "true" : "false")
           << ",\"skills\":[";

    bool first_skill = true;
    bool used_forms = false;
    void* forms_array = nullptr;
    std::size_t form_count = 0;
    if (safe_read_field(hero_type, "Forms", nullptr, forms_array) &&
        forms_array && safe_read(forms_array, 24, form_count) &&
        form_count > 0 && form_count <= 8) {
        auto* form_vector = static_cast<unsigned char*>(forms_array) + 32;
        for (std::size_t form_index = 0; form_index < form_count;
             ++form_index) {
            void* form = nullptr;
            if (!safe_read(form_vector, form_index * sizeof(void*), form) ||
                !form) {
                continue;
            }
            std::int32_t slot = 0;
            std::array<std::int32_t, 256> seen_type_ids{};
            std::size_t seen_count = 0;
            const std::array<const char*, 2> skill_id_fields = {
                "SkillTypeIds", "AdditionalSkillTypeIds"};
            for (const char* field_name : skill_id_fields) {
                void* skill_ids_list = nullptr;
                if (!safe_read_field(form, field_name, nullptr,
                                     skill_ids_list) ||
                    !skill_ids_list) {
                    continue;
                }
                const IntListItems skill_ids = read_int_list(skill_ids_list);
                for (std::size_t skill_index = 0;
                     skill_index < skill_ids.count; ++skill_index) {
                    const std::int32_t wanted_type_id =
                        skill_ids.values[skill_index];
                    bool duplicate = false;
                    for (std::size_t seen_index = 0;
                         seen_index < seen_count; ++seen_index) {
                        if (seen_type_ids[seen_index] == wanted_type_id) {
                            duplicate = true;
                            break;
                        }
                    }
                    if (duplicate) {
                        continue;
                    }
                    if (seen_count < seen_type_ids.size()) {
                        seen_type_ids[seen_count++] = wanted_type_id;
                    }
                    void* skill_type = find_skill_type_in_snapshot(
                        skill_types, wanted_type_id);
                    if (!skill_type) {
                        skill_type = find_skill_type_in_snapshot(
                            additional_skill_types, wanted_type_id);
                    }
                    if (skill_type && append_static_skill_json(
                                          output, skill_type, slot + 1,
                                          static_cast<std::int32_t>(form_index),
                                          first_skill)) {
                        ++slot;
                        used_forms = true;
                    }
                }
            }
        }
    }
    if (!used_forms) {
        std::int32_t slot = 0;
        for (std::size_t skill_index = 0; skill_index < skill_types.count;
             ++skill_index) {
            if (append_static_skill_json(output,
                                         skill_types.objects[skill_index],
                                         slot + 1, 0, first_skill)) {
                ++slot;
            }
        }
    }
    output << "]}";
    return true;
}

void append_static_hero_catalog(
    std::ostringstream& output,
    const std::array<std::int32_t, 6>& hero_ids,
    std::size_t hero_count) {
    output << '[';
    bool first_hero = true;
    const std::size_t used = (std::min)(hero_count, hero_ids.size());
    for (std::size_t hero_index = 0; hero_index < used; ++hero_index) {
        const std::int32_t hero_id = hero_ids[hero_index];
        append_static_hero_json(output, find_static_hero_type(hero_id),
                                hero_id, first_hero);
    }
    output << ']';
}

void append_static_hydra_head_catalog(std::ostringstream& output) {
    constexpr std::array<std::int32_t, 6> kHydraHeadTypeIds = {
        26040, 26080, 26120, 26160, 26200, 26240,
    };
    output << '[';
    bool first_head = true;
    for (const std::int32_t type_id : kHydraHeadTypeIds) {
        append_static_hero_json(output, find_static_hero_type(type_id),
                                type_id, first_head);
    }
    output << ']';
}

void append_all_static_hero_catalog(std::ostringstream& output) {
    output << '[';
    bool first_hero = true;
    void* section = static_data_section("HeroData");
    void* heroes_list = nullptr;
    Il2CppClass* list_class = nullptr;
    std::size_t items_offset = 0;
    std::size_t size_offset = 0;
    void* items = nullptr;
    std::int32_t size = 0;
    std::size_t array_length = 0;
    if (!section ||
        !safe_read_field(section, "HeroTypes", nullptr, heroes_list) ||
        !heroes_list || !safe_read(heroes_list, 0, list_class) ||
        !list_class ||
        !find_field_offset(list_class, "_items", "items", items_offset) ||
        !find_field_offset(list_class, "_size", "size", size_offset) ||
        !safe_read(heroes_list, items_offset, items) || !items ||
        !safe_read(heroes_list, size_offset, size) || size < 0 ||
        size > 16384 || !safe_read(items, 24, array_length) ||
        array_length > 16384) {
        output << ']';
        return;
    }
    const std::size_t used = (std::min)(static_cast<std::size_t>(size),
                                        array_length);
    auto* vector = static_cast<unsigned char*>(items) + 32;
    for (std::size_t index = 0; index < used; ++index) {
        void* hero_type = nullptr;
        Il2CppClass* hero_class = nullptr;
        std::int32_t hero_id = 0;
        bool is_boss = false;
        bool is_base = true;
        bool available = true;
        if (!safe_read(vector, index * sizeof(void*), hero_type) ||
            !hero_type || !safe_read(hero_type, 0, hero_class) ||
            !hero_class ||
            !safe_read_field(hero_type, "Id", nullptr, hero_id)) {
            continue;
        }
        const bool boss_known = runtime_get_bool(
            g_api.class_get_method_from_name(hero_class, "get_IsBoss", 0),
            hero_type, &is_boss);
        const bool base_known = runtime_get_bool(
            g_api.class_get_method_from_name(hero_class, "get_IsBaseType", 0),
            hero_type, &is_base);
        const bool available_known = runtime_get_bool(
            g_api.class_get_method_from_name(hero_class,
                                             "get_IsAvailableToUser", 0),
            hero_type, &available);
        if ((boss_known && is_boss) || (base_known && !is_base) ||
            (available_known && !available)) {
            continue;
        }
        append_static_hero_json(output, hero_type, hero_id, first_hero);
    }
    output << ']';
}

void append_model_effects(std::ostringstream& output, void* hero) {
    void* hero_state = nullptr;
    void* effects_list = nullptr;
    ObjectListItems effects{};
    if (safe_read_field(hero, "_heroState", nullptr, hero_state) && hero_state &&
        safe_read_field(hero_state, "AppliedEffects", nullptr, effects_list)) {
        effects = read_object_list(effects_list);
    }
    output << '[';
    for (std::size_t index = 0; index < effects.count; ++index) {
        if (index) {
            output << ',';
        }
        void* effect = effects.objects[index];
        std::int32_t id = 0;
        std::int32_t producer_id = 0;
        std::int32_t skill_type_id = 0;
        std::int32_t effect_type_id = 0;
        std::int32_t effect_kind_id = 0;
        std::int32_t effect_group_id = 0;
        std::int32_t apply_turn = 0;
        std::int32_t lifetime = 0;
        std::int32_t turns_left = 0;
        safe_read_field(effect, "Id", nullptr, id);
        safe_read_field(effect, "ProducerId", nullptr, producer_id);
        safe_read_field(effect, "SkillTypeId", nullptr, skill_type_id);
        safe_read_field(effect, "EffectTypeId", nullptr, effect_type_id);
        safe_read_field(effect, "ApplyTurn", nullptr, apply_turn);
        safe_read_field(effect, "Lifetime", nullptr, lifetime);
        safe_read_field(effect, "TurnLeft", nullptr, turns_left);
        void* effect_type = nullptr;
        if (safe_read_field(effect, "_type", nullptr, effect_type) &&
            effect_type) {
            safe_read_field(effect_type, "KindId", nullptr, effect_kind_id);
            safe_read_field(effect_type, "Group", nullptr, effect_group_id);
        }
        const std::string effect_kind_name =
            enum_member_name(g_effect_kind_id_class, effect_kind_id);
        output << "{\"id\":" << id
               << ",\"producerId\":" << producer_id
               << ",\"skillTypeId\":" << skill_type_id
               << ",\"effectTypeId\":" << effect_type_id
               << ",\"effectKindId\":" << effect_kind_id
               << ",\"effectKind\":\""
               << json_escape(effect_kind_name) << "\""
               << ",\"effectGroupId\":" << effect_group_id
               << ",\"applyTurn\":" << apply_turn
               << ",\"lifetime\":" << lifetime
               << ",\"turnsLeft\":" << turns_left << '}';
    }
    output << ']';
}

std::int32_t first_model_challenge_id(void* hero) {
    void* dictionary = nullptr;
    if (!hero ||
        !safe_read_field(hero, "Challenges", nullptr, dictionary) ||
        !dictionary) {
        return 0;
    }
    const IntObjectDictionaryItems challenges =
        read_int_object_dictionary(dictionary);
    return challenges.count ? challenges.keys[0] : 0;
}

struct ModelChallengeSnapshot {
    std::int32_t id{};
    void* challenge{};
    ChimeraTrialStaticIdentity identity{};
    bool started{};
    bool based_on_damage{};
    bool completed{};
    bool can_change_progress{};
    bool can_change_counter{};
    bool in_progress{};
    bool has_can_change_progress{};
    bool has_can_change_counter{};
    bool has_in_progress{};
    std::int64_t target_raw{};
    std::int64_t current_raw{};
    bool has_counter_limit{};
    std::int32_t counter_limit{};
    bool has_current_counter{};
    std::int32_t current_counter{};
    bool has_completed_turn{};
    std::int32_t completed_turn{};
};

void read_model_challenge_snapshot(ModelChallengeSnapshot& snapshot) {
    void* challenge = snapshot.challenge;
    safe_read_field(challenge, "IsStarted", nullptr, snapshot.started);
    safe_read_field(challenge, "BasedOnDamage", nullptr,
                    snapshot.based_on_damage);
    safe_read_field(challenge, "TargetProgress", nullptr,
                    snapshot.target_raw);
    safe_read_field(challenge, "CurrentProgress", nullptr,
                    snapshot.current_raw);
    read_nullable_int_field(challenge, "CounterLimit",
                            snapshot.has_counter_limit,
                            snapshot.counter_limit);
    read_nullable_int_field(challenge, "CurrentCounter",
                            snapshot.has_current_counter,
                            snapshot.current_counter);
    read_nullable_int_field(challenge, "SelfTurnWhichCompleted",
                            snapshot.has_completed_turn,
                            snapshot.completed_turn);
    Il2CppClass* challenge_class = nullptr;
    if (!safe_read(challenge, 0, challenge_class) || !challenge_class) {
        return;
    }
    const MethodInfo* completed_method =
        g_api.class_get_method_from_name(challenge_class,
                                         "get_IsCompleted", 0);
    const MethodInfo* can_change_progress_method =
        g_api.class_get_method_from_name(challenge_class,
                                         "get_CanChangeProgress", 0);
    const MethodInfo* can_change_counter_method =
        g_api.class_get_method_from_name(challenge_class,
                                         "get_CanChangeCounter", 0);
    const MethodInfo* in_progress_method =
        g_api.class_get_method_from_name(challenge_class,
                                         "get_InProgress", 0);
    if (completed_method) {
        runtime_get_bool(completed_method, challenge, &snapshot.completed);
    }
    snapshot.has_can_change_progress = can_change_progress_method &&
        runtime_get_bool(can_change_progress_method, challenge,
                         &snapshot.can_change_progress);
    snapshot.has_can_change_counter = can_change_counter_method &&
        runtime_get_bool(can_change_counter_method, challenge,
                         &snapshot.can_change_counter);
    snapshot.has_in_progress = in_progress_method &&
        runtime_get_bool(in_progress_method, challenge,
                         &snapshot.in_progress);
}

void append_model_challenges(std::ostringstream& output, void* hero) {
    void* dictionary = nullptr;
    std::int32_t chimera_turn_count = INT_MIN;
    std::int32_t chimera_form_id = INT_MIN;
    safe_read_field(hero, "TurnCount", nullptr, chimera_turn_count);
    safe_read_field(hero, "CurrentFormIndex", nullptr, chimera_form_id);
    const IntObjectDictionaryItems challenges =
        safe_read_field(hero, "Challenges", nullptr, dictionary)
        ? read_int_object_dictionary(dictionary)
        : IntObjectDictionaryItems{};
    std::array<ModelChallengeSnapshot, 256> snapshots{};
    const std::size_t snapshot_count =
        (std::min)(challenges.count, snapshots.size());
    for (std::size_t index = 0; index < snapshot_count; ++index) {
        ModelChallengeSnapshot& snapshot = snapshots[index];
        snapshot.id = challenges.keys[index];
        snapshot.challenge = challenges.objects[index];
        snapshot.identity = alliance_chimera_trial_identity(snapshot.id);
        read_model_challenge_snapshot(snapshot);
    }

    output << '[';
    for (std::size_t index = 0; index < snapshot_count; ++index) {
        if (index) {
            output << ',';
        }
        const ModelChallengeSnapshot& snapshot = snapshots[index];
        const ChimeraTrialStaticIdentity& identity = snapshot.identity;
        std::array<std::int32_t, 4> prerequisite_ids{};
        std::array<std::int32_t, 4> blocking_prerequisite_ids{};
        std::size_t prerequisite_count = 0;
        std::size_t blocking_prerequisite_count = 0;
        const bool has_chain_identity = identity.form_id > 0 &&
            identity.part_id > 0 && identity.challenge_difficulty_id > 0;
        if (has_chain_identity) {
            for (std::size_t other_index = 0;
                 other_index < snapshot_count; ++other_index) {
                const ModelChallengeSnapshot& other = snapshots[other_index];
                if (other.identity.form_id != identity.form_id ||
                    other.identity.part_id != identity.part_id ||
                    other.identity.challenge_difficulty_id <= 0 ||
                    other.identity.challenge_difficulty_id >=
                        identity.challenge_difficulty_id) {
                    continue;
                }
                if (prerequisite_count < prerequisite_ids.size()) {
                    prerequisite_ids[prerequisite_count++] = other.id;
                }
                if (!other.completed && blocking_prerequisite_count <
                        blocking_prerequisite_ids.size()) {
                    blocking_prerequisite_ids[
                        blocking_prerequisite_count++] = other.id;
                }
            }
        }
        const bool prerequisites_completed =
            blocking_prerequisite_count == 0;
        const bool unlocked = snapshot.completed ||
            (has_chain_identity && prerequisites_completed);
        const bool active_in_chain = !snapshot.completed &&
            has_chain_identity && prerequisites_completed;
        const bool matching_current_form = has_chain_identity &&
            chimera_form_id == identity.form_id;
        const bool eligible_now = active_in_chain && matching_current_form;
        const std::int32_t last_eligible_turn =
            chimera_last_rotation_turn_for_form(identity.form_id);
        const bool has_feasibility = identity.form_id > 0 &&
            last_eligible_turn > 0 && chimera_turn_count >= 0;
        const bool possible = snapshot.completed || !has_feasibility ||
            chimera_turn_count <= last_eligible_turn;
        const double ratio = snapshot.target_raw > 0
            ? static_cast<double>(snapshot.current_raw) /
                  static_cast<double>(snapshot.target_raw)
            : 0.0;
        constexpr double kFixedOne = 4294967296.0;
        output << "{\"id\":" << snapshot.id
               << ",\"started\":"
               << (snapshot.started ? "true" : "false")
               << ",\"completed\":"
               << (snapshot.completed ? "true" : "false")
               << ",\"basedOnDamage\":"
               << (snapshot.based_on_damage ? "true" : "false")
               << ",\"targetRaw\":" << snapshot.target_raw
               << ",\"currentRaw\":" << snapshot.current_raw
               << ",\"target\":"
               << (static_cast<double>(snapshot.target_raw) / kFixedOne)
               << ",\"current\":"
               << (static_cast<double>(snapshot.current_raw) / kFixedOne)
               << ",\"progressRatio\":" << ratio;
        if (identity.form_id > 0) {
            output << ",\"formId\":" << identity.form_id
                   << ",\"form\":\""
                   << json_escape(enum_member_name(
                          g_chimera_form_class, identity.form_id))
                   << "\"";
        }
        if (identity.part_id > 0) {
            output << ",\"partId\":" << identity.part_id
                   << ",\"part\":\""
                   << json_escape(enum_member_name(
                          g_chimera_challenge_part_class,
                          identity.part_id))
                   << "\"";
        }
        if (identity.challenge_difficulty_id > 0) {
            output << ",\"difficultyId\":"
                   << identity.challenge_difficulty_id
                   << ",\"difficulty\":\""
                   << json_escape(enum_member_name(
                          g_chimera_challenge_difficulty_class,
                          identity.challenge_difficulty_id))
                   << "\"";
        }
        if (has_chain_identity) {
            output << ",\"requiredPrerequisiteTrialIds\":[";
            for (std::size_t prerequisite_index = 0;
                 prerequisite_index < prerequisite_count;
                 ++prerequisite_index) {
                if (prerequisite_index) {
                    output << ',';
                }
                output << prerequisite_ids[prerequisite_index];
            }
            output << "],\"blockingPrerequisiteTrialIds\":[";
            for (std::size_t prerequisite_index = 0;
                 prerequisite_index < blocking_prerequisite_count;
                 ++prerequisite_index) {
                if (prerequisite_index) {
                    output << ',';
                }
                output << blocking_prerequisite_ids[prerequisite_index];
            }
            output << "],\"unlocked\":"
                   << (unlocked ? "true" : "false")
                   << ",\"activeInChain\":"
                   << (active_in_chain ? "true" : "false")
                   << ",\"matchingCurrentForm\":"
                   << (matching_current_form ? "true" : "false")
                   << ",\"eligibleNow\":"
                   << (eligible_now ? "true" : "false")
                   << ",\"chainState\":\""
                   << (snapshot.completed
                           ? "completed"
                           : (active_in_chain ? "active" : "locked"))
                   << "\"";
        }
        if (snapshot.has_can_change_progress) {
            output << ",\"canChangeProgress\":"
                   << (snapshot.can_change_progress ? "true" : "false");
        }
        if (snapshot.has_can_change_counter) {
            output << ",\"canChangeCounter\":"
                   << (snapshot.can_change_counter ? "true" : "false");
        }
        if (snapshot.has_in_progress) {
            output << ",\"inProgress\":"
                   << (snapshot.in_progress ? "true" : "false");
        }
        if (has_feasibility) {
            output << ",\"lastEligibleBossTurn\":" << last_eligible_turn
                   << ",\"possible\":"
                   << (possible ? "true" : "false")
                   << ",\"impossible\":"
                   << (possible ? "false" : "true");
            if (!possible) {
                output << ",\"impossibilityReason\":"
                          "\"last_form_window_expired\"";
            }
        }
        if (snapshot.has_counter_limit) {
            output << ",\"counterLimit\":" << snapshot.counter_limit;
        }
        if (snapshot.has_current_counter) {
            output << ",\"currentCounter\":"
                   << snapshot.current_counter;
        }
        if (snapshot.has_completed_turn) {
            output << ",\"selfTurnWhichCompleted\":"
                   << snapshot.completed_turn;
        }
        output << '}';
    }
    output << ']';
}

void append_actor_state(std::ostringstream& output, void* mode,
                        const IntObjectDictionaryItems& actors,
                        const char* side) {
    output << '[';
    for (std::size_t index = 0; index < actors.count; ++index) {
        if (index) {
            output << ',';
        }
        const std::int32_t actor_id = actors.keys[index];
        std::int32_t type_id = actor_hero_type_id(actors.objects[index]);
        void* hero = find_battle_hero(mode, actor_id);
        std::int32_t model_type_id = 0;
        std::int32_t current_form_index = 0;
        std::int32_t turn_count = 0;
        std::int64_t health_raw = 0;
        std::int64_t max_health_raw = 0;
        bool dead = false;
        bool active = false;
        bool stunned = false;
        bool frozen = false;
        bool sleep = false;
        bool provoked = false;
        bool active_skills_blocked = false;
        bool duel_producer = false;
        bool duel_target = false;
        bool enfeeble = false;
        bool rages = false;
        bool is_hydra_head = false;
        bool is_hydra_neck = false;
        std::int32_t devoured_hero_id = -1;
        std::int32_t digestion_turns = -1;
        std::int32_t battle_position = 0;
        void* hero_state = nullptr;
        if (hero) {
            safe_read_field(hero, "TypeId", nullptr, model_type_id);
            if (model_type_id > 0) {
                type_id = model_type_id;
            }
            safe_read_field(hero, "TurnCount", nullptr, turn_count);
            safe_read_field(hero, "CurrentFormIndex", nullptr,
                            current_form_index);
            safe_read_field(hero, "Health", nullptr, health_raw);
            Il2CppClass* hero_class = nullptr;
            if (safe_read(hero, 0, hero_class) && hero_class) {
                const MethodInfo* max_health_method =
                    g_api.class_get_method_from_name(hero_class,
                                                     "get_MaxHealth", 0);
                runtime_get_fixed_raw(max_health_method, hero,
                                      &max_health_raw);
                runtime_get_bool(g_api.class_get_method_from_name(
                                     hero_class, "get_IsHydraHead", 0),
                                 hero, &is_hydra_head);
                runtime_get_bool(g_api.class_get_method_from_name(
                                     hero_class, "get_IsHydraNeck", 0),
                                 hero, &is_hydra_neck);
            }
            safe_read_field(hero, "IsHydraHead", nullptr, is_hydra_head);
            safe_read_field(hero, "IsHydraNeck", nullptr, is_hydra_neck);
            safe_read_field(hero, "DevouredHeroId", nullptr,
                            devoured_hero_id);
            if (!safe_read_field(hero, "BattlePosition", nullptr,
                                 battle_position)) {
                if (!safe_read_field(hero, "TeamPosition", nullptr,
                                     battle_position)) {
                    safe_read_field(hero, "Slot", nullptr,
                                    battle_position);
                }
            }
            void* digestion_info = nullptr;
            if (safe_read_field(hero, "DigestionInfo", nullptr,
                                digestion_info) && digestion_info) {
                if (!safe_read_field(digestion_info, "TurnsLeft", nullptr,
                                     digestion_turns)) {
                    if (!safe_read_field(digestion_info, "RemainingTurns",
                                         nullptr, digestion_turns)) {
                        safe_read_field(digestion_info, "TurnCount", nullptr,
                                        digestion_turns);
                    }
                }
            }
            if (safe_read_field(hero, "_heroState", nullptr, hero_state) &&
                hero_state) {
                safe_read_field(hero_state, "IsDead", nullptr, dead);
                safe_read_field(hero_state, "IsStunned", nullptr, stunned);
                safe_read_field(hero_state, "IsFrozen", nullptr, frozen);
                safe_read_field(hero_state, "IsSleep", nullptr, sleep);
                safe_read_field(hero_state, "IsProvoked", nullptr, provoked);
                safe_read_field(hero_state, "ActiveSkillsBlocked", nullptr,
                                active_skills_blocked);
                safe_read_field(hero_state, "IsDuelProducer", nullptr,
                                duel_producer);
                safe_read_field(hero_state, "IsDuelTarget", nullptr,
                                duel_target);
                safe_read_field(hero_state, "IsEnfeeble", nullptr, enfeeble);
                safe_read_field(hero_state, "IsRages", nullptr, rages);
                Il2CppClass* state_class = nullptr;
                if (safe_read(hero_state, 0, state_class) && state_class) {
                    const MethodInfo* active_method =
                        g_api.class_get_method_from_name(state_class,
                                                         "get_IsActive", 0);
                    runtime_get_bool(active_method, hero_state, &active);
                }
            }
        }
        const ResolvedText name = resolve_static_hero_name(type_id);
        const std::string avatar =
            resolve_static_hero_avatar(type_id, current_form_index);
        const double health_percent = max_health_raw > 0
            ? static_cast<double>(health_raw) * 100.0 /
                  static_cast<double>(max_health_raw)
            : 0.0;
        output << "{\"id\":" << actor_id
               << ",\"typeId\":" << type_id
               << ",\"nameKey\":\"" << json_escape(name.key)
               << "\",\"name\":\"" << json_escape(name.display)
               << "\",\"avatar\":\"" << json_escape(avatar)
               << "\",\"side\":\"" << side << "\""
               << ",\"modelFound\":" << (hero ? "true" : "false")
               << ",\"currentFormIndex\":" << current_form_index
               << ",\"turnCount\":" << turn_count
               << ",\"healthRaw\":" << health_raw
               << ",\"maxHealthRaw\":" << max_health_raw
               << ",\"healthPct\":" << health_percent
               << ",\"dead\":" << (dead ? "true" : "false")
               << ",\"active\":" << (active ? "true" : "false")
               << ",\"isHydraHead\":"
               << (is_hydra_head ? "true" : "false")
               << ",\"isHydraNeck\":"
               << (is_hydra_neck ? "true" : "false")
               << ",\"headState\":\""
               << (is_hydra_neck
                       ? "exposed_neck"
                       : (is_hydra_head ? "head" : ""))
               << "\""
               << ",\"devouredHeroId\":" << devoured_hero_id
               << ",\"isDevouring\":"
               << (devoured_hero_id >= 0 ? "true" : "false")
               << ",\"digestionTurns\":" << digestion_turns
               << ",\"battlePosition\":" << battle_position
               << ",\"states\":{\"stunned\":"
               << (stunned ? "true" : "false")
               << ",\"frozen\":" << (frozen ? "true" : "false")
               << ",\"sleep\":" << (sleep ? "true" : "false")
               << ",\"provoked\":" << (provoked ? "true" : "false")
               << ",\"activeSkillsBlocked\":"
               << (active_skills_blocked ? "true" : "false")
               << ",\"duelProducer\":"
               << (duel_producer ? "true" : "false")
               << ",\"duelTarget\":" << (duel_target ? "true" : "false")
               << ",\"enfeeble\":" << (enfeeble ? "true" : "false")
               << ",\"rages\":" << (rages ? "true" : "false") << '}';
        output << ",\"skills\":";
        append_model_skills(output, hero);
        output << ",\"effects\":";
        append_model_effects(output, hero);
        output << ",\"challenges\":";
        append_model_challenges(output, hero);
        output << '}';
    }
    output << ']';
}

void append_int_ids(std::ostringstream& output,
                    const IntDictionaryKeys& ids) {
    output << '[';
    for (std::size_t index = 0; index < ids.count; ++index) {
        if (index) {
            output << ',';
        }
        output << ids.values[index];
    }
    output << ']';
}

struct BattleRuntimeState {
    bool valid{};
    std::int32_t area_id{INT_MIN};
    std::int32_t region_id{INT_MIN};
    std::int32_t kind_id{};
    std::int32_t round{};
    std::int32_t turn{};
    std::int32_t player_turn_count{};
    std::int32_t player_auto_turn_count{};
    bool finished{};
    bool auto_mode{};
    bool extra_turn{};
    bool chimera_preset{};
    bool hydra_battle{};
    bool reported_hydra_battle{};
    bool active_hero_valid{};
    std::int32_t active_hero_id{};
    std::int32_t active_hero_type_id{};
    std::int32_t active_hero_turn_count{};
    std::int32_t active_hero_form_index{};
    std::int32_t active_hero_skills_update_counter{};
    bool active_hero_is_metamorph{};
    bool active_hero_is_transformed{};
    std::int32_t chimera_id{};
    std::int32_t chimera_type_id{};
    bool chimera_present{};
    std::int32_t chimera_form_index{};
    std::int32_t chimera_turn_count{};
};

bool battle_model_objects(void* mode, void** context, void** state) {
    void* processor = nullptr;
    *context = nullptr;
    *state = nullptr;
    return safe_read(mode, 16, processor) && processor &&
           safe_read_field(processor, "<Context>k__BackingField", "Context",
                           *context) && *context &&
           safe_read_field(*context, "State", nullptr, *state) && *state;
}

struct ChimeraBattleMetrics {
    bool valid{};
    double damage{};
    std::int64_t competition_points{};
    const char* source{"unavailable"};
    std::size_t sample_count{};
};

ChimeraBattleMetrics read_chimera_battle_metrics(void* mode) {
    ChimeraBattleMetrics metrics{};
    void* context = nullptr;
    void* state = nullptr;
    if (!battle_model_objects(mode, &context, &state)) {
        return metrics;
    }
    void* statistics_by_multiplier = nullptr;
    if (safe_read_field(state, "ChimeraStatisticsByMultiplier", nullptr,
                        statistics_by_multiplier) &&
        statistics_by_multiplier) {
        const ObjectListItems multiplier_statistics =
            read_object_dictionary_values(statistics_by_multiplier);
        std::int64_t total_damage_raw = 0;
        for (std::size_t index = 0;
             index < multiplier_statistics.count; ++index) {
            std::int64_t damage_raw = 0;
            if (safe_read_field(multiplier_statistics.objects[index],
                                "Damage", nullptr, damage_raw)) {
                total_damage_raw += damage_raw;
            }
        }
        constexpr double kFixedOne = 4294967296.0;
        metrics.damage = static_cast<double>(total_damage_raw) / kFixedOne;
        metrics.valid = multiplier_statistics.valid;
        metrics.source = "chimera_statistics";
        metrics.sample_count = multiplier_statistics.count;
    }
    void* points = nullptr;
    if (safe_read_field(state, "ChimeraCompetitionPoints", nullptr, points) &&
        points) {
        Il2CppClass* points_class = nullptr;
        if (safe_read(points, 0, points_class) && points_class) {
            const MethodInfo* to_long =
                g_api.class_get_method_from_name(points_class, "ToLong", 0);
            std::int64_t current_points = 0;
            if (runtime_get_fixed_raw(to_long, points, &current_points)) {
                metrics.competition_points = current_points;
                metrics.valid = true;
            }
        }
    }
    return metrics;
}

ChimeraBattleMetrics read_hydra_battle_metrics(void* mode) {
    ChimeraBattleMetrics metrics{};
    std::int64_t ui_total_damage = 0;
    std::size_t ui_head_count = 0;
    if (read_hydra_ui_damage(&ui_total_damage, &ui_head_count)) {
        metrics.damage = static_cast<double>(ui_total_damage);
        metrics.valid = true;
        metrics.source = "hydra_ui_counter";
        metrics.sample_count = ui_head_count;
        return metrics;
    }

    void* context = nullptr;
    void* state = nullptr;
    if (!battle_model_objects(mode, &context, &state)) {
        return metrics;
    }

    // In RAID 11.70 / Unity 6000.3, HydraTotalTakenDamage is overloaded for
    // BattleHero and BattleHeroSnapshot.  The BattleStateSnapshot overload
    // assumed by the earlier implementation does not exist.  Read the four
    // current model heroes, then retain each object's monotonic contribution
    // so a replacement head starts a new contribution instead of erasing the
    // damage dealt to the previous object.
    void* bosses_dictionary = nullptr;
    if (!safe_read(mode, 128, bosses_dictionary)) {
        return metrics;
    }
    const IntObjectDictionaryItems bosses =
        read_int_object_dictionary(bosses_dictionary);
    if (!bosses.valid) {
        return metrics;
    }

    std::int64_t accumulated_raw = -1;
    bool used_extension = false;
    bool used_field_fallback = false;
    for (std::size_t index = 0; index < bosses.count; ++index) {
        const std::int32_t actor_id = bosses.keys[index];
        void* hero = find_battle_hero(mode, actor_id);
        if (!hero) {
            continue;
        }
        std::int64_t head_damage_raw = 0;
        bool damage_read = runtime_get_fixed_raw_one_object(
            g_hydra_total_damage_method, hero, &head_damage_raw);
        used_extension = used_extension || damage_read;
        if (!damage_read) {
            damage_read = safe_read_field(
                hero, "DamageTaken", nullptr, head_damage_raw);
            used_field_fallback = used_field_fallback || damage_read;
        }
        if (!damage_read || head_damage_raw < 0) {
            continue;
        }
        const std::int64_t observed = observe_hydra_damage(
            context, actor_id, hero, head_damage_raw);
        if (observed >= 0) {
            accumulated_raw = observed;
            ++metrics.sample_count;
        }
    }

    if (metrics.sample_count > 0 && accumulated_raw >= 0) {
        constexpr double kFixedOne = 4294967296.0;
        metrics.damage = static_cast<double>(accumulated_raw) / kFixedOne;
        metrics.valid = true;
        metrics.source = used_extension
            ? "hydra_total_taken_damage"
            : (used_field_fallback ? "hydra_damage_taken_field"
                                   : "unavailable");
    }
    return metrics;
}

ChimeraBattleMetrics read_alliance_boss_metrics(
    void* mode, const BattleRuntimeState& runtime) {
    return runtime.hydra_battle
        ? read_hydra_battle_metrics(mode)
        : read_chimera_battle_metrics(mode);
}

void* find_battle_hero(void* mode, std::int32_t actor_id) {
    void* context = nullptr;
    void* state = nullptr;
    Il2CppClass* state_class = nullptr;
    if (actor_id < 0 || !battle_model_objects(mode, &context, &state) ||
        !safe_read(state, 0, state_class) || !state_class) {
        return nullptr;
    }
    const MethodInfo* find_hero =
        find_one_int_method(state_class, "FindHero");
    void* hero = nullptr;
    return safe_runtime_invoke_one_int(find_hero, state, actor_id, &hero)
        ? hero
        : nullptr;
}

BattleRuntimeState read_battle_runtime_state(void* mode) {
    BattleRuntimeState snapshot{};
    void* context = nullptr;
    void* state = nullptr;
    if (!battle_model_objects(mode, &context, &state)) {
        return snapshot;
    }

    Il2CppClass* context_class = nullptr;
    if (safe_read(context, 0, context_class) && context_class) {
        const MethodInfo* area_method = g_api.class_get_method_from_name(
            context_class, "get_CurrentAreaTypeId", 0);
        const MethodInfo* region_method = g_api.class_get_method_from_name(
            context_class, "get_CurrentRegionTypeId", 0);
        runtime_get_int(area_method, context, &snapshot.area_id);
        runtime_get_int(region_method, context, &snapshot.region_id);
    }

    safe_read_field(state, "_battleKindId", nullptr, snapshot.kind_id);
    safe_read_field(state, "CurrentRound", nullptr, snapshot.round);
    safe_read_field(state, "CurrentTurn", nullptr, snapshot.turn);
    safe_read_field(state, "PlayerTurnCount", nullptr,
                    snapshot.player_turn_count);
    safe_read_field(state, "PlayerAutoTurnCount", nullptr,
                    snapshot.player_auto_turn_count);
    safe_read_field(state, "BattleFinished", nullptr, snapshot.finished);
    safe_read_field(state, "IsAutoBattleMode", nullptr, snapshot.auto_mode);
    safe_read_field(state, "IsExtraTurn", nullptr, snapshot.extra_turn);
    safe_read_field(state, "IsChimeraPreset", nullptr,
                    snapshot.chimera_preset);
    safe_read_field(state, "IsHydraBattle", nullptr,
                    snapshot.reported_hydra_battle);
    snapshot.hydra_battle = snapshot.reported_hydra_battle;

    void* active_hero = nullptr;
    if (safe_read_field(state, "ActiveHero", nullptr, active_hero) &&
        active_hero) {
        const bool id_read = safe_read_field(
            active_hero, "<Id>k__BackingField", "Id",
            snapshot.active_hero_id);
        const bool type_read = safe_read_field(
            active_hero, "TypeId", nullptr,
            snapshot.active_hero_type_id);
        snapshot.active_hero_valid = id_read && type_read &&
            snapshot.active_hero_id >= 0 &&
            snapshot.active_hero_type_id > 0;
        safe_read_field(active_hero, "TurnCount", nullptr,
                        snapshot.active_hero_turn_count);
        safe_read_field(active_hero, "CurrentFormIndex", nullptr,
                        snapshot.active_hero_form_index);
        safe_read_field(active_hero, "CurrentSkillsUpdateCounter", nullptr,
                        snapshot.active_hero_skills_update_counter);
        Il2CppClass* active_hero_class = nullptr;
        if (safe_read(active_hero, 0, active_hero_class) && active_hero_class) {
            runtime_get_bool(g_api.class_get_method_from_name(
                                 active_hero_class, "get_IsMetamorph", 0),
                             active_hero,
                             &snapshot.active_hero_is_metamorph);
            bool model_is_transformed = false;
            runtime_get_bool(g_api.class_get_method_from_name(
                                 active_hero_class, "get_IsTransformed", 0),
                             active_hero, &model_is_transformed);
            (void)model_is_transformed;
        }
        // get_IsTransformed is false outside the short transition phase on
        // some mythical heroes. CurrentFormIndex is the persistent battle
        // state and is therefore authoritative for strategy decisions.
        snapshot.active_hero_is_transformed =
            snapshot.active_hero_form_index != 0;
    }

    Il2CppClass* state_class = nullptr;
    void* chimera = nullptr;
    if (safe_read(state, 0, state_class) && state_class) {
        const MethodInfo* chimera_method =
            g_api.class_get_method_from_name(state_class, "Chimera", 0);
        safe_runtime_invoke_object(chimera_method, state, &chimera);
    }
    if (chimera) {
        snapshot.chimera_present = true;
        safe_read_field(chimera, "<Id>k__BackingField", "Id",
                        snapshot.chimera_id);
        safe_read_field(chimera, "TypeId", nullptr,
                        snapshot.chimera_type_id);
        safe_read_field(chimera, "CurrentFormIndex", nullptr,
                        snapshot.chimera_form_index);
        safe_read_field(chimera, "TurnCount", nullptr,
                        snapshot.chimera_turn_count);
    }
    snapshot.valid = true;
    return snapshot;
}

void capture_battle_state(void* mode, void* skill_data,
                          void* acceptable_targets, void* actors_dictionary,
                          void* bosses_dictionary) {
    const IntDictionaryKeys valid_targets =
        read_int_dictionary_keys(acceptable_targets);
    const IntDictionaryKeys boss_ids =
        read_int_dictionary_keys(bosses_dictionary);
    const IntObjectDictionaryItems actors =
        read_int_object_dictionary(actors_dictionary);
    const IntObjectDictionaryItems bosses =
        read_int_object_dictionary(bosses_dictionary);

    std::int32_t skill_id = -1;
    std::int32_t level = 0;
    std::int32_t cooldown = -1;
    bool passive = false;
    std::int32_t skill_type_id = 0;
    std::int32_t hero_type_id = 0;
    std::int32_t default_cooldown = 0;
    safe_read(skill_data, 16, skill_id);
    safe_read(skill_data, 20, level);
    safe_read(skill_data, 24, cooldown);
    safe_read(skill_data, 28, passive);
    safe_read(skill_data, 32, skill_type_id);
    safe_read(skill_data, 36, hero_type_id);
    safe_read(skill_data, 64, default_cooldown);

    void* generator = nullptr;
    bool waiting = false;
    safe_read(mode, 104, generator);
    safe_read(generator, 56, waiting);

    std::int32_t active_actor_id = 0;
    std::size_t matching_actors = 0;
    for (std::size_t index = 0; index < actors.count; ++index) {
        if (actor_hero_type_id(actors.objects[index]) == hero_type_id) {
            active_actor_id = actors.keys[index];
            ++matching_actors;
        }
    }
    if (matching_actors != 1) {
        active_actor_id = 0;
    }

    BattleRuntimeState runtime = read_battle_runtime_state(mode);
    const AllianceBossIdentity boss_identity = identify_alliance_boss(
        runtime.active_hero_valid, runtime.active_hero_type_id,
        runtime.chimera_preset, runtime.reported_hydra_battle,
        runtime.chimera_present, bosses.count);
    runtime.hydra_battle =
        boss_identity.mode == kTakeoverBossModeHydra;
    const ChimeraBattleMetrics metrics =
        read_alliance_boss_metrics(mode, runtime);
    if (runtime.active_hero_valid) {
        for (std::size_t index = 0; index < actors.count; ++index) {
            if (actors.keys[index] == runtime.active_hero_id) {
                active_actor_id = runtime.active_hero_id;
                break;
            }
        }
    }

    std::int32_t area_id = runtime.area_id;
    std::int32_t region_id = runtime.region_id;
    bool chimera_enabled = false;
    bool boss_current = !boss_ids.valid || boss_ids.count > 0;
    void* context = g_battle_context.load(std::memory_order_acquire);
    if (context && object_derives_from(context, "ClientBattleViewContext")) {
        // The Hydra view context reports 0 for these identifiers on some
        // screens.  The battle-model values above are authoritative; use the
        // view only as a fallback when the model did not expose a value.
        if (area_id == INT_MIN) {
            runtime_get_int(g_area_type_method, context, &area_id);
        }
        if (region_id == INT_MIN) {
            runtime_get_int(g_region_type_method, context, &region_id);
        }
        runtime_get_bool(g_chimera_enabled_method, context, &chimera_enabled);
        runtime_get_bool(g_enemy_boss_current_method, context, &boss_current);
    }

    const ResolvedText hero_name = resolve_static_hero_name(hero_type_id);
    const ResolvedText skill_name = resolve_static_skill_name(skill_type_id);
    const std::uint64_t sequence =
        g_state_sequence.fetch_add(1, std::memory_order_acq_rel) + 1;

    std::ostringstream output;
    output << "battle_state {\"type\":\"battle_state\",\"sequence\":"
           << sequence << ",\"pid\":" << GetCurrentProcessId()
           << ",\"bossMode\":\""
           << (runtime.hydra_battle ? "hydra" : "chimera") << "\""
           << ",\"battle\":{\"runtimeStateValid\":"
           << (runtime.valid ? "true" : "false")
           << ",\"areaTypeId\":";
    if (area_id == INT_MIN) {
        output << "null";
    } else {
        output << area_id;
    }
    output << ",\"regionTypeId\":";
    if (region_id == INT_MIN) {
        output << "null";
    } else {
        output << region_id;
    }
    output << ",\"kindId\":" << runtime.kind_id
           << ",\"round\":" << runtime.round
           << ",\"turn\":" << runtime.turn
           << ",\"playerTurnCount\":" << runtime.player_turn_count
           << ",\"playerAutoTurnCount\":"
           << runtime.player_auto_turn_count
           << ",\"finished\":" << (runtime.finished ? "true" : "false")
           << ",\"autoMode\":" << (runtime.auto_mode ? "true" : "false")
           << ",\"extraTurn\":" << (runtime.extra_turn ? "true" : "false")
           << ",\"chimeraPreset\":"
           << (runtime.chimera_preset ? "true" : "false")
           << ",\"hydraBattle\":"
           << (runtime.hydra_battle ? "true" : "false")
           << ",\"chimeraCompetitionEnabled\":"
           << (chimera_enabled ? "true" : "false")
           << ",\"bossPresent\":" << (boss_current ? "true" : "false")
           << ",\"waitingForManualCommand\":"
           << (waiting ? "true" : "false")
           << ",\"metricsAvailable\":"
           << (metrics.valid ? "true" : "false")
           << ",\"currentDamage\":" << metrics.damage
           << ",\"metricsSource\":\"" << metrics.source << "\""
           << ",\"metricsSampleCount\":" << metrics.sample_count
           << ",\"currentCompetitionPoints\":"
           << metrics.competition_points << "}"
           << ",\"activeHeroId\":" << active_actor_id
           << ",\"activeHeroTypeId\":" << hero_type_id
           << ",\"modelActiveHeroId\":" << runtime.active_hero_id
           << ",\"modelActiveHeroTypeId\":"
           << runtime.active_hero_type_id
           << ",\"activeHeroTurnCount\":"
           << runtime.active_hero_turn_count
           << ",\"activeHeroNameKey\":\"" << json_escape(hero_name.key)
           << "\",\"activeHeroName\":\""
           << json_escape(hero_name.display) << "\""
           << ",\"selectedSkill\":{\"slot\":" << (skill_id + 1)
           << ",\"skillId\":" << skill_id
           << ",\"typeId\":" << skill_type_id
           << ",\"nameKey\":\"" << json_escape(skill_name.key)
           << "\",\"name\":\"" << json_escape(skill_name.display)
           << "\",\"level\":" << level
           << ",\"cooldown\":" << cooldown
           << ",\"defaultCooldown\":" << default_cooldown
           << ",\"passive\":" << (passive ? "true" : "false")
           << ",\"validTargetIds\":";
    append_int_ids(output, valid_targets);
    output << "},\"heroes\":";
    append_actor_state(output, mode, actors, "ally");
    output << ",\"bosses\":";
    append_actor_state(output, mode, bosses, "enemy");
    output << ",\"chimera\":{\"id\":" << runtime.chimera_id
           << ",\"typeId\":" << runtime.chimera_type_id
           << ",\"currentFormIndex\":" << runtime.chimera_form_index
           << ",\"currentForm\":\""
           << json_escape(enum_member_name(g_chimera_form_class,
                                           runtime.chimera_form_index))
           << "\""
           << ",\"turnCount\":" << runtime.chimera_turn_count << '}';
    output << ",\"bossTargetIds\":";
    append_int_ids(output, boss_ids);
    output << '}';
    diagnostic(output.str());
}

void append_decision_skills(std::ostringstream& output, void* mode,
                            std::int32_t active_hero_type_id) {
    const ObjectListItems skills = active_skill_data_snapshot();
    output << '[';
    bool first = true;
    for (std::size_t index = 0; index < skills.count; ++index) {
        void* skill_data = skills.objects[index];
        if (!object_has_class_name(skill_data, "SkillData")) {
            continue;
        }
        std::int32_t skill_id = -1;
        std::int32_t type_id = 0;
        std::int32_t hero_type_id = 0;
        std::int32_t cooldown = -1;
        std::int32_t default_cooldown = 0;
        bool passive = true;
        bool blocked = true;
        void* live_name_object = nullptr;
        void* description_object = nullptr;
        void* icon_object = nullptr;
        safe_read_field(skill_data, "Id", nullptr, skill_id);
        safe_read_field(skill_data, "TypeId", nullptr, type_id);
        safe_read_field(skill_data, "HeroTypeId", nullptr, hero_type_id);
        safe_read_field(skill_data, "Cooldown", nullptr, cooldown);
        safe_read_field(skill_data, "DefaultCooldown", nullptr,
                        default_cooldown);
        safe_read_field(skill_data, "IsPassive", nullptr, passive);
        safe_read_field(skill_data, "IsBlockedSkill", nullptr, blocked);
        safe_read_field(skill_data, "Name", nullptr, live_name_object);
        safe_read_field(skill_data, "Description", nullptr,
                        description_object);
        safe_read_field(skill_data, "Icon", nullptr, icon_object);
        if (hero_type_id != active_hero_type_id || skill_id < 0 ||
            type_id <= 0) {
            continue;
        }

        IntDictionaryKeys valid_targets{};
        void* targets = nullptr;
        if (!passive && !blocked && cooldown == 0) {
            safe_get_acceptable_targets(mode, skill_data, &targets);
            valid_targets = read_int_dictionary_keys(targets);
        }
        const ResolvedText static_name = resolve_static_skill_name(type_id);
        const std::string live_name = il2cpp_string_utf8(live_name_object);
        const std::string description =
            il2cpp_string_utf8(description_object);
        const std::string icon = il2cpp_string_utf8(icon_object);
        const std::string display_name =
            live_name.empty() ? static_name.display : live_name;
        if (!first) {
            output << ',';
        }
        first = false;
        output << "{\"slot\":" << (skill_id + 1)
               << ",\"skillId\":" << skill_id
               << ",\"typeId\":" << type_id
               << ",\"nameKey\":\"" << json_escape(static_name.key)
               << "\",\"name\":\"" << json_escape(display_name)
               << "\",\"description\":\"" << json_escape(description)
               << "\",\"icon\":\"" << json_escape(icon)
               << "\",\"cooldown\":" << cooldown
               << ",\"defaultCooldown\":" << default_cooldown
               << ",\"passive\":" << (passive ? "true" : "false")
               << ",\"blocked\":" << (blocked ? "true" : "false")
               << ",\"ready\":"
               << ((!passive && !blocked && cooldown == 0 &&
                    valid_targets.valid && valid_targets.count > 0)
                       ? "true" : "false")
               << ",\"skillDataPtr\":"
               << reinterpret_cast<std::uintptr_t>(skill_data)
               << ",\"validTargetIds\":";
        append_int_ids(output, valid_targets);
        output << '}';
    }
    output << ']';
}

void capture_decision_state(void* generator) {
    void* mode = g_battle_mode.load(std::memory_order_acquire);
    void* active_generator = nullptr;
    bool waiting = false;
    if (!mode || !object_derives_from(mode, "ClientBattleMode") ||
        !safe_read(mode, 104, active_generator) ||
        active_generator != generator ||
        !safe_read(generator, 56, waiting)) {
        diagnostic("decision_state_skipped reason=mode_or_generator_unavailable");
        return;
    }

    BattleRuntimeState runtime = read_battle_runtime_state(mode);
    void* actors_dictionary = nullptr;
    void* bosses_dictionary = nullptr;
    safe_read(mode, 112, actors_dictionary);
    safe_read(mode, 128, bosses_dictionary);
    const IntObjectDictionaryItems actors =
        read_int_object_dictionary(actors_dictionary);
    const IntObjectDictionaryItems bosses =
        read_int_object_dictionary(bosses_dictionary);
    const AllianceBossIdentity boss_identity = identify_alliance_boss(
        runtime.active_hero_valid, runtime.active_hero_type_id,
        runtime.chimera_preset, runtime.reported_hydra_battle,
        runtime.chimera_present, bosses.count);
    runtime.hydra_battle =
        boss_identity.mode == kTakeoverBossModeHydra;
    const ChimeraBattleMetrics metrics =
        read_alliance_boss_metrics(mode, runtime);
    const bool active_chimera =
        boss_identity.mode == kTakeoverBossModeChimera;
    const bool active_hydra =
        boss_identity.mode == kTakeoverBossModeHydra;
    if (!runtime.valid || !runtime.active_hero_valid ||
        runtime.active_hero_type_id <= 0 ||
        (!active_chimera && !active_hydra) || runtime.finished) {
        std::ostringstream message;
        message << "decision_state_skipped reason=not_active_alliance_boss"
                << " runtime_valid=" << (runtime.valid ? 1 : 0)
                << " active_hero_valid="
                << (runtime.active_hero_valid ? 1 : 0)
                << " active_hero_id=" << runtime.active_hero_id
                << " active_hero_type_id=" << runtime.active_hero_type_id
                << " area_id=" << runtime.area_id
                << " region_id=" << runtime.region_id
                << " kind_id=" << runtime.kind_id
                << " chimera_preset=" << (runtime.chimera_preset ? 1 : 0)
                << " reported_hydra_battle="
                << (runtime.reported_hydra_battle ? 1 : 0)
                << " boss_count=" << bosses.count
                << " requested_boss_mode="
                << boss_identity.requested_mode
                << " identified_boss_mode=" << boss_identity.mode
                << " started_selection_valid="
                << (boss_identity.started_selection_valid ? 1 : 0)
                << " started_mode_matches="
                << (boss_identity.started_mode_matches ? 1 : 0)
                << " active_hero_matches_team="
                << (boss_identity.active_hero_matches_team ? 1 : 0)
                << " finished=" << (runtime.finished ? 1 : 0)
                << " round=" << runtime.round
                << " turn=" << runtime.turn
                << " player_turn_count=" << runtime.player_turn_count;
        diagnostic(message.str());
        return;
    }

    if (metrics.valid) {
        if (metrics.damage > 0) {
            g_last_chimera_damage.store(
                static_cast<std::int64_t>(metrics.damage + 0.5),
                std::memory_order_release);
        }
        if (metrics.competition_points > 0) {
            g_last_chimera_competition_points.store(
                metrics.competition_points, std::memory_order_release);
        }
    }

    if (g_screen_state.load(std::memory_order_acquire) != kScreenBattle) {
        g_selection_context.store(nullptr, std::memory_order_release);
        g_result_context.store(nullptr, std::memory_order_release);
        g_screen_state.store(kScreenBattle, std::memory_order_release);
        publish_lifecycle(active_hydra
                              ? "hydra_battle_verified"
                              : "chimera_battle_verified");
    }

    const bool skill_catalog_fresh =
        g_skill_catalog_active_hero_id.load(std::memory_order_acquire) ==
            runtime.active_hero_id &&
        g_skill_catalog_active_hero_type_id.load(std::memory_order_acquire) ==
            runtime.active_hero_type_id &&
        g_skill_catalog_active_hero_turn_count.load(
            std::memory_order_acquire) == runtime.active_hero_turn_count &&
        g_skill_catalog_active_hero_form_index.load(
            std::memory_order_acquire) == runtime.active_hero_form_index &&
        g_skill_catalog_active_hero_skills_update_counter.load(
            std::memory_order_acquire) ==
            runtime.active_hero_skills_update_counter;
    if (!skill_catalog_fresh) {
        diagnostic("decision_state_skill_catalog_stale");
    }

    bool active_actor_present = false;
    for (std::size_t index = 0; index < actors.count; ++index) {
        active_actor_present = active_actor_present ||
            actors.keys[index] == runtime.active_hero_id;
    }
    if (!active_actor_present) {
        diagnostic("decision_state_skipped reason=active_actor_not_in_ui");
        return;
    }
    const ResolvedText hero_name =
        resolve_static_hero_name(runtime.active_hero_type_id);
    void* chimera_model = active_chimera && runtime.chimera_present
        ? find_battle_hero(mode, runtime.chimera_id)
        : nullptr;
    const std::int32_t first_trial_id =
        first_model_challenge_id(chimera_model);
    const std::int32_t alliance_difficulty =
        alliance_chimera_difficulty_for_trial_id(first_trial_id);
    const std::uint64_t sequence =
        g_state_sequence.fetch_add(1, std::memory_order_acq_rel) + 1;

    std::ostringstream output;
    output << "{\"type\":\"decision_state\",\"sequence\":"
           << sequence << ",\"pid\":" << GetCurrentProcessId()
           << ",\"bossMode\":\""
           << (runtime.hydra_battle ? "hydra" : "chimera") << "\""
           << ",\"observedAtTick\":" << GetTickCount64()
           << ",\"battle\":{\"areaTypeId\":" << runtime.area_id
           << ",\"regionTypeId\":" << runtime.region_id
           << ",\"kindId\":" << runtime.kind_id
           << ",\"round\":" << runtime.round
           << ",\"turn\":" << runtime.turn
           << ",\"playerTurnCount\":" << runtime.player_turn_count
           << ",\"finished\":" << (runtime.finished ? "true" : "false")
           << ",\"autoMode\":" << (runtime.auto_mode ? "true" : "false")
           << ",\"chimeraPreset\":"
           << (runtime.chimera_preset ? "true" : "false")
           << ",\"hydraBattle\":"
           << (runtime.hydra_battle ? "true" : "false")
           << ",\"reportedHydraBattle\":"
           << (runtime.reported_hydra_battle ? "true" : "false")
           << ",\"identityEvidence\":{\"requestedMode\":"
           << boss_identity.requested_mode
           << ",\"startedSelectionValid\":"
           << (boss_identity.started_selection_valid ? "true" : "false")
           << ",\"startedModeMatches\":"
           << (boss_identity.started_mode_matches ? "true" : "false")
           << ",\"activeHeroMatchesTeam\":"
           << (boss_identity.active_hero_matches_team ? "true" : "false")
           << ",\"bossCount\":" << boss_identity.boss_count << '}'
           << ",\"waitingForManualCommand\":"
           << (waiting ? "true" : "false")
           << ",\"metricsAvailable\":"
           << (metrics.valid ? "true" : "false")
           << ",\"currentDamage\":" << metrics.damage
           << ",\"metricsSource\":\"" << metrics.source << "\""
           << ",\"metricsSampleCount\":" << metrics.sample_count
           << ",\"currentCompetitionPoints\":"
           << metrics.competition_points << "}"
           << ",\"activeHeroId\":" << runtime.active_hero_id
           << ",\"activeHeroTypeId\":" << runtime.active_hero_type_id
           << ",\"activeHeroTurnCount\":"
           << runtime.active_hero_turn_count
           << ",\"activeHeroFormIndex\":"
           << runtime.active_hero_form_index
           << ",\"activeHeroSkillsUpdateCounter\":"
           << runtime.active_hero_skills_update_counter
           << ",\"activeHeroIsMetamorph\":"
           << (runtime.active_hero_is_metamorph ? "true" : "false")
           << ",\"activeHeroIsTransformed\":"
           << (runtime.active_hero_is_transformed ? "true" : "false")
           << ",\"skillCatalogFresh\":"
           << (skill_catalog_fresh ? "true" : "false")
           << ",\"activeHeroNameKey\":\"" << json_escape(hero_name.key)
           << "\",\"activeHeroName\":\""
           << json_escape(hero_name.display) << "\""
           << ",\"pointers\":{\"context\":"
           << reinterpret_cast<std::uintptr_t>(
                  g_battle_context.load(std::memory_order_acquire))
           << ",\"generator\":" << reinterpret_cast<std::uintptr_t>(generator)
           << ",\"mode\":" << reinterpret_cast<std::uintptr_t>(mode)
           << "},\"skills\":";
    if (skill_catalog_fresh) {
        append_decision_skills(output, mode, runtime.active_hero_type_id);
    } else {
        output << "[]";
    }
    output << ",\"heroes\":";
    append_actor_state(output, mode, actors, "ally");
    output << ",\"bosses\":";
    append_actor_state(output, mode, bosses, "enemy");
    output << ",\"chimera\":{\"id\":" << runtime.chimera_id
           << ",\"typeId\":" << runtime.chimera_type_id
           << ",\"currentFormIndex\":" << runtime.chimera_form_index
           << ",\"currentForm\":\""
           << json_escape(enum_member_name(g_chimera_form_class,
                                           runtime.chimera_form_index))
           << "\",\"turnCount\":" << runtime.chimera_turn_count << '}'
           << ",\"hydra\":{\"active\":"
           << (runtime.hydra_battle ? "true" : "false")
           << ",\"turnCount\":" << runtime.turn << '}'
           << ",\"allianceChimeraDifficultyId\":"
           << alliance_difficulty;
    SelectionSnapshot started_selection{};
    AcquireSRWLockShared(&g_ui_state_lock);
    started_selection = g_last_started_selection;
    ReleaseSRWLockShared(&g_ui_state_lock);
    if (started_selection.valid && started_selection.stage_id > 0) {
        output << ",\"chimeraStageId\":" << started_selection.stage_id;
    }
    std::string trial_catalog;
    std::string rotation_identity;
    AcquireSRWLockShared(&g_chimera_catalog_lock);
    if (alliance_difficulty > 0 &&
        static_cast<std::size_t>(alliance_difficulty) <
            g_chimera_catalog_by_difficulty.size() &&
        !g_chimera_catalog_by_difficulty[alliance_difficulty].empty()) {
        trial_catalog =
            g_chimera_catalog_by_difficulty[alliance_difficulty];
    }
    rotation_identity = g_chimera_rotation_identity_json;
    ReleaseSRWLockShared(&g_chimera_catalog_lock);
    output << ",\"rotationIdentity\":"
           << (rotation_identity.empty() ? "null" : rotation_identity)
           << ",\"trialCatalog\":"
           << (trial_catalog.empty() ? "null" : trial_catalog);
    output << '}';
    if (g_shared_state) {
        publish_shared_json(g_shared_state->decision, output.str());
    }
    diagnostic("decision_state_published sequence=" +
               std::to_string(sequence));
}

bool resolve_selected_hero_type_id(void* selection_context,
                                   void* selection_user,
                                   std::int32_t hero_instance_id,
                                   const MethodInfo* hero_method,
                                   std::int32_t* hero_type_id) {
    if (!selection_context || !selection_user || hero_instance_id <= 0 ||
        !hero_type_id || !hero_method) {
        return false;
    }
    void* hero = nullptr;
    if (!safe_runtime_invoke_pointer_int(
            hero_method, selection_context, selection_user,
            hero_instance_id, &hero) || !hero) {
        return false;
    }
    std::int32_t type_id = 0;
    bool type_read = safe_read_field(
        hero, "TypeId", "<TypeId>k__BackingField", type_id);
    if (!type_read) {
        Il2CppClass* hero_class = nullptr;
        if (safe_read(hero, 0, hero_class) && hero_class) {
            type_read = runtime_get_int(
                g_api.class_get_method_from_name(hero_class, "get_TypeId", 0),
                hero, &type_id);
        }
    }
    if (!type_read || type_id <= 0 || !find_static_hero_type(type_id)) {
        return false;
    }
    *hero_type_id = type_id;
    return true;
}

bool selection_snapshots_equal(const SelectionSnapshot& left,
                               const SelectionSnapshot& right) {
    return left.valid == right.valid && left.filled == right.filled &&
           left.auto_battle == right.auto_battle &&
           left.quick_battle == right.quick_battle &&
           left.hydra == right.hydra && left.area_id == right.area_id &&
           left.stage_id == right.stage_id &&
           left.hero_count == right.hero_count &&
           left.hero_ids == right.hero_ids &&
           left.hero_type_ids == right.hero_type_ids;
}

void capture_selection_state(void* self, const char* reason,
                             void* selection_user,
                             bool publish_unchanged) {
    const bool is_chimera = self && object_has_class_name(
        self, "HeroesSelectionChimeraDialogContext");
    const bool is_hydra = self && object_has_class_name(
        self, "HeroesSelectionHydraDialogContext");
    if (!is_chimera && !is_hydra) {
        return;
    }
    void* acquired_selection_user = nullptr;
    if (selection_user) {
        g_selection_user.store(selection_user, std::memory_order_release);
    } else if (safe_runtime_invoke_nullable_int_none(
                   g_app_model_read_user_method,
                   refresh_app_model_instance(),
                   &acquired_selection_user)) {
        // UserReadGuard is valid only for its acquisition scope.  HeroPicked
        // does not provide a guard and does not necessarily trigger Refresh,
        // so delayed selection capture must acquire a fresh one rather than
        // reusing the disposed guard from an earlier Refresh callback.
        selection_user = acquired_selection_user;
    }
    const MethodInfo* filled_method = is_hydra
        ? g_hydra_selection_filled_method
        : g_selection_filled_method;
    const MethodInfo* heroes_method = is_hydra
        ? g_hydra_selection_heroes_method
        : g_selection_heroes_method;
    const MethodInfo* hero_method = is_hydra
        ? g_hydra_selection_hero_method
        : g_selection_hero_method;
    const MethodInfo* area_method = is_hydra
        ? g_hydra_selection_area_method
        : g_selection_area_method;
    const MethodInfo* stage_method = is_hydra
        ? g_hydra_selection_stage_method
        : g_selection_stage_method;
    const MethodInfo* quick_method = is_hydra
        ? g_hydra_quick_battle_active_method
        : g_quick_battle_active_method;
    SelectionSnapshot snapshot{};
    snapshot.hydra = is_hydra;
    SelectionSnapshot previous{};
    AcquireSRWLockShared(&g_ui_state_lock);
    previous = g_selection_snapshot;
    ReleaseSRWLockShared(&g_ui_state_lock);
    void* heroes_array = nullptr;
    void* auto_checkbox = nullptr;
    void* quick_checkbox = nullptr;
    const bool filled_read = runtime_get_bool(
        filled_method, self, &snapshot.filled);
    const bool heroes_read = safe_runtime_invoke_object(
        heroes_method, self, &heroes_array);
    const IntArraySnapshot heroes = read_int_array(heroes_array);
    const bool area_read = runtime_get_int(
        area_method, self, &snapshot.area_id);
    const bool stage_read = runtime_get_int(
        stage_method, self, &snapshot.stage_id);
    const bool auto_object_read = safe_read_field(
        self, "_autoBattleCheckbox", nullptr, auto_checkbox);
    const bool quick_object_read = safe_read_field(
        self, "_quickBattleCheckbox", nullptr, quick_checkbox);
    const bool auto_read = auto_object_read && auto_checkbox &&
        runtime_get_bool(g_auto_battle_selected_method, auto_checkbox,
                         &snapshot.auto_battle);
    const bool quick_read = quick_object_read && quick_checkbox &&
        runtime_get_bool(quick_method, quick_checkbox,
                         &snapshot.quick_battle);
    snapshot.hero_count =
        (std::min)(heroes.count, snapshot.hero_ids.size());
    for (std::size_t index = 0; index < snapshot.hero_count; ++index) {
        snapshot.hero_ids[index] = heroes.values[index];
        if (selection_user) {
            resolve_selected_hero_type_id(
                self, selection_user, snapshot.hero_ids[index],
                hero_method,
                &snapshot.hero_type_ids[index]);
        }
        if (snapshot.hero_type_ids[index] <= 0) {
            for (std::size_t previous_index = 0;
                 previous_index < previous.hero_count; ++previous_index) {
                if (previous.hero_ids[previous_index] ==
                    snapshot.hero_ids[index]) {
                    snapshot.hero_type_ids[index] =
                        previous.hero_type_ids[previous_index];
                    break;
                }
            }
        }
    }
    if (acquired_selection_user) {
        if (!safe_runtime_invoke_void(g_user_read_guard_dispose_method,
                                      acquired_selection_user)) {
            diagnostic("selection_user_guard_dispose_failed");
        }
        acquired_selection_user = nullptr;
        selection_user = nullptr;
    }
    snapshot.valid = filled_read && heroes_read && heroes.valid && area_read &&
                     stage_read && auto_read && quick_read;
    if (snapshot.valid) {
        for (std::size_t index = 0; index < snapshot.hero_count; ++index) {
            const bool has_hero = snapshot.hero_ids[index] > 0;
            const bool has_type = snapshot.hero_type_ids[index] > 0;
            if (has_hero != has_type) {
                snapshot.valid = false;
            }
            for (std::size_t previous = 0; previous < index; ++previous) {
                if (has_hero &&
                    snapshot.hero_ids[index] == snapshot.hero_ids[previous]) {
                    snapshot.valid = false;
                }
            }
        }
    }
    AcquireSRWLockExclusive(&g_ui_state_lock);
    g_selection_snapshot = snapshot;
    ReleaseSRWLockExclusive(&g_ui_state_lock);
    g_selection_context.store(self, std::memory_order_release);
    g_result_context.store(nullptr, std::memory_order_release);
    g_screen_state.store(kScreenTeamSelection, std::memory_order_release);
    if (!publish_unchanged && selection_snapshots_equal(snapshot, previous)) {
        return;
    }
    if (g_shared_state) {
        std::ostringstream catalog;
        catalog << "{\"type\":\"hero_catalog_state\",\"heroes\":";
        append_static_hero_catalog(catalog, snapshot.hero_type_ids,
                                   snapshot.hero_count);
        catalog << '}';
        publish_shared_json(g_shared_state->decision, catalog.str());
        publish_shared_json(g_shared_state->battle_ledger, "");
    }
    publish_lifecycle(reason ? reason : "team_selection_observed");
    diagnostic("team_selection_observed valid=" +
               std::to_string(snapshot.valid ? 1 : 0) +
               " filled=" + std::to_string(snapshot.filled ? 1 : 0) +
               " heroes=" + std::to_string(snapshot.hero_count) +
               " hero_types=" +
               std::to_string(std::count_if(
                   snapshot.hero_type_ids.begin(),
                   snapshot.hero_type_ids.begin() + snapshot.hero_count,
                   [](std::int32_t value) { return value > 0; })) +
               " auto=" + std::to_string(snapshot.auto_battle ? 1 : 0) +
               " quick=" + std::to_string(snapshot.quick_battle ? 1 : 0));
}

void capture_result_state(void* self, std::int64_t returned_damage) {
    const bool is_chimera = self && object_has_class_name(
        self, "BattleFinishAllianceChimeraDialogContext");
    const bool is_hydra = self && object_has_class_name(
        self, "BattleFinishAllianceHydraDialogContext");
    if (!is_chimera && !is_hydra) {
        return;
    }
    std::int64_t damage = returned_damage;
    std::int64_t stored_damage = 0;
    std::int64_t points = 0;
    std::int32_t completed = -1;
    if (safe_read_field(self, "Damage", nullptr, stored_damage) &&
        stored_damage >= 0) {
        damage = stored_damage;
    }
    safe_read_field(self, "CompetitionPoints", nullptr, points);
    if (damage <= 0) {
        damage = g_last_chimera_damage.load(std::memory_order_acquire);
    }
    if (points <= 0) {
        points = g_last_chimera_competition_points.load(
            std::memory_order_acquire);
    }
    if (is_chimera) {
        runtime_get_int(g_result_completed_challenges_method, self,
                        &completed);
    }
    g_result_context.store(self, std::memory_order_release);
    g_selection_context.store(nullptr, std::memory_order_release);
    g_screen_state.store(kScreenResult, std::memory_order_release);
    if (g_shared_state) {
        std::ostringstream output;
        output << "{\"type\":\"battle_ledger\",\"pid\":"
               << GetCurrentProcessId()
               << ",\"bossMode\":\""
               << (is_hydra ? "hydra" : "chimera") << "\""
               << ",\"screen\":\"result\",\"damage\":" << damage
               << ",\"competitionPoints\":" << points
               << ",\"completedChallengeCount\":" << completed
               << ",\"disposition\":\"awaiting_user\""
               << ",\"resultSaved\":false,\"automaticRestart\":false"
               << ",\"observedAtTick\":" << GetTickCount64() << '}';
        publish_shared_json(g_shared_state->battle_ledger, output.str());
    }
    publish_lifecycle("result_screen_observed");
    diagnostic(std::string(is_hydra ? "hydra" : "chimera") +
               "_result_observed damage=" + std::to_string(damage) +
               " points=" + std::to_string(points) +
               " completed=" + std::to_string(completed));
}

void __fastcall hook_selection_on_enabled(void* self,
                                           const MethodInfo* method) {
    g_original_selection_on_enabled(self, method);
    g_selection_user.store(nullptr, std::memory_order_release);
    g_last_chimera_damage.store(0, std::memory_order_release);
    g_last_chimera_competition_points.store(0, std::memory_order_release);
    reset_hydra_damage_tracker();
    refresh_chimera_rotation_catalog(false);
    capture_selection_state(self, "team_selection_enabled");
    if (g_game_window) {
        SetTimer(g_game_window, kSelectionCaptureTimerId,
                 kSelectionCaptureIntervalMs, nullptr);
    }
}

void __fastcall hook_selection_on_disabled(void* self,
                                            const MethodInfo* method) {
    g_original_selection_on_disabled(self, method);
    if (g_selection_context.load(std::memory_order_acquire) == self) {
        if (g_game_window) {
            KillTimer(g_game_window, kSelectionCaptureTimerId);
        }
        g_selection_context.store(nullptr, std::memory_order_release);
        g_selection_user.store(nullptr, std::memory_order_release);
        AcquireSRWLockExclusive(&g_ui_state_lock);
        g_selection_snapshot = {};
        ReleaseSRWLockExclusive(&g_ui_state_lock);
        if (g_screen_state.load(std::memory_order_acquire) ==
            kScreenTeamSelection) {
            g_screen_state.store(kScreenUnknown, std::memory_order_release);
        }
        publish_lifecycle("team_selection_disabled");
    }
}

void __fastcall hook_selection_refresh(void* self, void* user,
                                       const MethodInfo* method) {
    g_original_selection_refresh(self, user, method);
    capture_selection_state(self, "team_selection_refreshed", user);
    if (g_game_window) {
        SetTimer(g_game_window, kSelectionCaptureTimerId,
                 kSelectionCaptureIntervalMs, nullptr);
    }
}

void __fastcall hook_selection_start_battle_click(void* self,
                                                   const MethodInfo* method) {
    AcquireSRWLockExclusive(&g_ui_state_lock);
    g_last_started_selection = g_selection_snapshot;
    ReleaseSRWLockExclusive(&g_ui_state_lock);
    g_original_selection_start_battle_click(self, method);
}

void __fastcall hook_hydra_selection_on_enabled(void* self,
                                                 const MethodInfo* method) {
    g_original_hydra_selection_on_enabled(self, method);
    g_selection_user.store(nullptr, std::memory_order_release);
    g_last_chimera_damage.store(0, std::memory_order_release);
    g_last_chimera_competition_points.store(0, std::memory_order_release);
    reset_hydra_damage_tracker();
    capture_selection_state(self, "hydra_team_selection_enabled");
    if (g_game_window) {
        SetTimer(g_game_window, kSelectionCaptureTimerId,
                 kSelectionCaptureIntervalMs, nullptr);
    }
}

void __fastcall hook_hydra_selection_on_disabled(void* self,
                                                  const MethodInfo* method) {
    g_original_hydra_selection_on_disabled(self, method);
    if (g_selection_context.load(std::memory_order_acquire) == self) {
        if (g_game_window) {
            KillTimer(g_game_window, kSelectionCaptureTimerId);
        }
        g_selection_context.store(nullptr, std::memory_order_release);
        g_selection_user.store(nullptr, std::memory_order_release);
        AcquireSRWLockExclusive(&g_ui_state_lock);
        g_selection_snapshot = {};
        ReleaseSRWLockExclusive(&g_ui_state_lock);
        if (g_screen_state.load(std::memory_order_acquire) ==
            kScreenTeamSelection) {
            g_screen_state.store(kScreenUnknown, std::memory_order_release);
        }
        publish_lifecycle("hydra_team_selection_disabled");
    }
}

void __fastcall hook_hydra_selection_refresh(void* self, void* user,
                                              const MethodInfo* method) {
    g_original_hydra_selection_refresh(self, user, method);
    capture_selection_state(self, "hydra_team_selection_refreshed", user);
    if (g_game_window) {
        SetTimer(g_game_window, kSelectionCaptureTimerId,
                 kSelectionCaptureIntervalMs, nullptr);
    }
}

void __fastcall hook_hydra_selection_start_battle_click(
    void* self, const MethodInfo* method) {
    AcquireSRWLockExclusive(&g_ui_state_lock);
    g_last_started_selection = g_selection_snapshot;
    ReleaseSRWLockExclusive(&g_ui_state_lock);
    g_original_hydra_selection_start_battle_click(self, method);
}

std::int64_t __fastcall hook_result_total_damage(void* self,
                                                 const MethodInfo* method) {
    const std::int64_t damage = g_original_result_total_damage(self, method);
    capture_result_state(self, damage);
    return damage;
}

std::int64_t __fastcall hook_hydra_result_total_damage(
    void* self, const MethodInfo* method) {
    const std::int64_t damage =
        g_original_hydra_result_total_damage(self, method);
    capture_result_state(self, damage);
    return damage;
}

void __fastcall hook_hydra_damage_counter_change(
    void* self, std::int64_t value, std::int32_t head_id,
    const MethodInfo* method) {
    g_original_hydra_damage_counter_change(self, value, head_id, method);
    // HydraDamageCounter keeps Dictionary<int, long>[headId] at the greatest
    // value observed for that head and displays the sum. Mirror that exact
    // signal instead of tying battle damage to replaceable BattleHero objects.
    observe_hydra_ui_damage(head_id, value);
}

void __fastcall hook_on_enabled(void* self, const MethodInfo* method) {
    g_original_on_enabled(self, method);
    if (g_game_window) {
        KillTimer(g_game_window, kSelectionCaptureTimerId);
    }
    capture_battle_context(self);
    g_selection_context.store(nullptr, std::memory_order_release);
    g_selection_user.store(nullptr, std::memory_order_release);
    g_result_context.store(nullptr, std::memory_order_release);
    g_screen_state.store(kScreenUnknown, std::memory_order_release);
    if (g_shared_state) {
        publish_shared_json(g_shared_state->decision, "");
        publish_shared_json(g_shared_state->battle_ledger, "");
    }
    publish_lifecycle("battle_context_enabled_unverified");
}

void __fastcall hook_on_disabled(void* self, const MethodInfo* method) {
    g_original_on_disabled(self, method);
    if (g_battle_context.load(std::memory_order_acquire) == self) {
        g_battle_context.store(nullptr, std::memory_order_release);
        g_command_generator.store(nullptr, std::memory_order_release);
        g_battle_processor.store(nullptr, std::memory_order_release);
        g_battle_mode.store(nullptr, std::memory_order_release);
        g_battle_hud_context.store(nullptr, std::memory_order_release);
        clear_active_skill_data();
        clear_executed_turn();
        if (g_shared_state) {
            publish_shared_json(g_shared_state->decision, "");
        }
        g_screen_state.store(kScreenUnknown, std::memory_order_release);
        publish_lifecycle("battle_context_disabled");
        diagnostic("battle_context_cleared");
    }
}

void __fastcall hook_request_command(void* self, const MethodInfo* method) {
    g_original_request_command(self, method);
    capture_generator_state("request_command", self);
    g_command_generator.store(self, std::memory_order_release);
    if (!g_game_window ||
        !SetTimer(g_game_window, kDecisionCaptureTimerId, 75, nullptr)) {
        capture_decision_state(self);
    }
}

void __fastcall hook_push_active_skill_data(void* self, void* skill_data_list,
                                             const MethodInfo* method) {
    g_original_push_active_skill_data(self, skill_data_list, method);
    g_battle_hud_context.store(self, std::memory_order_release);
    void* mode = g_battle_mode.load(std::memory_order_acquire);
    const BattleRuntimeState runtime = read_battle_runtime_state(mode);
    if (runtime.valid && runtime.active_hero_valid) {
        const bool catalog_identity_changed =
            g_skill_catalog_active_hero_id.load(std::memory_order_acquire) !=
                runtime.active_hero_id ||
            g_skill_catalog_active_hero_type_id.load(
                std::memory_order_acquire) != runtime.active_hero_type_id ||
            g_skill_catalog_active_hero_form_index.load(
                std::memory_order_acquire) != runtime.active_hero_form_index ||
            g_skill_catalog_active_hero_skills_update_counter.load(
                std::memory_order_acquire) !=
                runtime.active_hero_skills_update_counter;
        if (catalog_identity_changed) {
            clear_active_skill_data();
        }
        remember_active_skill_data(skill_data_list);
        g_skill_catalog_active_hero_id.store(runtime.active_hero_id,
                                             std::memory_order_release);
        g_skill_catalog_active_hero_type_id.store(
            runtime.active_hero_type_id, std::memory_order_release);
        g_skill_catalog_active_hero_turn_count.store(
            runtime.active_hero_turn_count, std::memory_order_release);
        g_skill_catalog_active_hero_form_index.store(
            runtime.active_hero_form_index, std::memory_order_release);
        g_skill_catalog_active_hero_skills_update_counter.store(
            runtime.active_hero_skills_update_counter,
            std::memory_order_release);
    }
    diagnostic("active_skill_data_pushed count=" +
               std::to_string(active_skill_data_snapshot().count));
    void* generator = g_command_generator.load(std::memory_order_acquire);
    if (generator) {
        if (!g_game_window ||
            !SetTimer(g_game_window, kDecisionCaptureTimerId, 75, nullptr)) {
            capture_decision_state(generator);
        }
    }
}

void __fastcall hook_battle_hud_pause_click(void* self,
                                             const MethodInfo* method) {
    diagnostic("battle_hud_pause_clicked");
    if (g_takeover_state.load(std::memory_order_acquire) == 1 &&
        g_screen_state.load(std::memory_order_acquire) == kScreenBattle) {
        interrupt_takeover("game_pause_clicked",
                           "battle_hud_pause_button");
    }
    g_original_battle_hud_pause_click(self, method);
}

void __fastcall hook_select_skill(void* self, std::int32_t skill_id,
                                  const MethodInfo* method) {
    diagnostic("select_skill skill_id=" + std::to_string(skill_id));
    g_original_select_skill(self, skill_id, method);
    capture_generator_state("select_skill_state", self);
}

void __fastcall hook_select_target(void* self, std::int32_t actor_id,
                                   const MethodInfo* method) {
    diagnostic("select_target actor_id=" + std::to_string(actor_id));
    g_original_select_target(self, actor_id, method);
    capture_generator_state("select_target_state", self);
}

void __fastcall hook_mode_select_skill(void* self, void* skill_data,
                                       const MethodInfo* method) {
    std::ostringstream message;
    message << "ui_select_skill mode=" << self << " skill_data=" << skill_data;
    diagnostic(message.str());
    g_battle_mode.store(self, std::memory_order_release);
    remember_single_skill_data(skill_data);
    capture_mode_state("ui_select_skill_state", self);
    capture_skill_data("ui_select_skill_data_before", skill_data);
    g_original_mode_select_skill(self, skill_data, method);
    capture_skill_data("ui_select_skill_data_after", skill_data);
}

void __fastcall hook_context_select_target(void* self, std::int32_t hero_id,
                                           const MethodInfo* method) {
    diagnostic("ui_select_target hero_id=" + std::to_string(hero_id));
    capture_battle_context(self);
    g_original_context_select_target(self, hero_id, method);
}

void* __fastcall hook_get_acceptable_targets(void* self, void* skill_data,
                                             const MethodInfo* method) {
    g_battle_mode.store(self, std::memory_order_release);
    remember_single_skill_data(skill_data);
    void* result = g_original_get_acceptable_targets(self, skill_data, method);
    capture_skill_data("acceptable_targets_skill", skill_data);
    diagnostic_dictionary_keys("acceptable_target_ids", result);

    void* actors = nullptr;
    void* bosses = nullptr;
    safe_read(self, 112, actors);
    safe_read(self, 128, bosses);
    diagnostic_dictionary_keys("actor_ui_ids", actors);
    diagnostic_dictionary_keys("boss_ui_ids", bosses);
    std::int32_t skill_type_id = 0;
    safe_read(skill_data, 32, skill_type_id);
    diagnostic_actor_names("actor_name", actors, skill_type_id);
    diagnostic_actor_names("boss_name", bosses, skill_type_id);
    capture_battle_state(self, skill_data, result, actors, bosses);
    void* generator = nullptr;
    if (safe_read(self, 104, generator) && generator) {
        capture_decision_state(generator);
    }
    return result;
}

void __fastcall hook_create_manual_command(void* self, std::int32_t target_id,
                                           std::int32_t skill_id,
                                           const MethodInfo* method) {
    diagnostic("create_manual_command target_id=" + std::to_string(target_id) +
               " skill_id=" + std::to_string(skill_id));
    capture_generator_state("create_manual_command_state", self);
    g_original_create_manual_command(self, target_id, skill_id, method);
}

void* native_method_pointer(const MethodLocation& location) {
    if (!location.method) {
        return nullptr;
    }
    void* pointer = *reinterpret_cast<void* const*>(location.method);
    MEMORY_BASIC_INFORMATION memory{};
    if (!pointer || !VirtualQuery(pointer, &memory, sizeof(memory)) ||
        memory.State != MEM_COMMIT) {
        return nullptr;
    }
    const DWORD executable = PAGE_EXECUTE | PAGE_EXECUTE_READ |
                             PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    return (memory.Protect & executable) ? pointer : nullptr;
}

bool install_capture_hooks(const MethodLocation& on_enabled,
                           const MethodLocation& on_disabled,
                           const MethodLocation& request_command,
                           const MethodLocation& select_skill,
                           const MethodLocation& select_target,
                           const MethodLocation& mode_select_skill,
                           const MethodLocation& push_active_skill_data,
                           const MethodLocation& battle_hud_pause_click,
                           const MethodLocation& context_select_target,
                           const MethodLocation& get_acceptable_targets,
                           const MethodLocation& create_manual_command,
                           const MethodLocation& selection_on_enabled,
                           const MethodLocation& selection_on_disabled,
                           const MethodLocation& selection_refresh,
                           const MethodLocation& selection_start_battle_click,
                           const MethodLocation& result_total_damage) {
    void* enabled_pointer = native_method_pointer(on_enabled);
    void* disabled_pointer = native_method_pointer(on_disabled);
    void* request_pointer = native_method_pointer(request_command);
    void* select_skill_pointer = native_method_pointer(select_skill);
    void* select_target_pointer = native_method_pointer(select_target);
    void* mode_select_skill_pointer = native_method_pointer(mode_select_skill);
    void* push_active_skill_data_pointer =
        native_method_pointer(push_active_skill_data);
    void* battle_hud_pause_click_pointer =
        native_method_pointer(battle_hud_pause_click);
    void* context_select_target_pointer = native_method_pointer(context_select_target);
    void* acceptable_targets_pointer = native_method_pointer(get_acceptable_targets);
    void* create_manual_pointer = native_method_pointer(create_manual_command);
    void* selection_enabled_pointer =
        native_method_pointer(selection_on_enabled);
    void* selection_disabled_pointer =
        native_method_pointer(selection_on_disabled);
    void* selection_refresh_pointer =
        native_method_pointer(selection_refresh);
    void* selection_start_pointer =
        native_method_pointer(selection_start_battle_click);
    void* result_total_damage_pointer =
        native_method_pointer(result_total_damage);
    if (!enabled_pointer || !disabled_pointer || !request_pointer ||
        !select_skill_pointer || !select_target_pointer ||
        !mode_select_skill_pointer || !push_active_skill_data_pointer ||
        !battle_hud_pause_click_pointer ||
        !context_select_target_pointer ||
        !acceptable_targets_pointer || !create_manual_pointer ||
        !selection_enabled_pointer || !selection_disabled_pointer ||
        !selection_refresh_pointer ||
        !selection_start_pointer || !result_total_damage_pointer) {
        diagnostic("hook_target_pointer_invalid");
        return false;
    }

    MH_STATUS status = MH_Initialize();
    if (status != MH_OK && status != MH_ERROR_ALREADY_INITIALIZED) {
        diagnostic("minhook_initialize_failed status=" + std::to_string(status));
        return false;
    }
    status = MH_CreateHook(enabled_pointer, &hook_on_enabled,
                           reinterpret_cast<void**>(&g_original_on_enabled));
    if (status != MH_OK) {
        diagnostic("hook_on_enabled_create_failed status=" + std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(disabled_pointer, &hook_on_disabled,
                           reinterpret_cast<void**>(&g_original_on_disabled));
    if (status != MH_OK) {
        diagnostic("hook_on_disabled_create_failed status=" + std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(request_pointer, &hook_request_command,
                           reinterpret_cast<void**>(&g_original_request_command));
    if (status != MH_OK) {
        diagnostic("hook_request_command_create_failed status=" + std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(select_skill_pointer, &hook_select_skill,
                           reinterpret_cast<void**>(&g_original_select_skill));
    if (status != MH_OK) {
        diagnostic("hook_select_skill_create_failed status=" + std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(select_target_pointer, &hook_select_target,
                           reinterpret_cast<void**>(&g_original_select_target));
    if (status != MH_OK) {
        diagnostic("hook_select_target_create_failed status=" + std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(mode_select_skill_pointer, &hook_mode_select_skill,
                           reinterpret_cast<void**>(&g_original_mode_select_skill));
    if (status != MH_OK) {
        diagnostic("hook_mode_select_skill_create_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(
        push_active_skill_data_pointer, &hook_push_active_skill_data,
        reinterpret_cast<void**>(&g_original_push_active_skill_data));
    if (status != MH_OK) {
        diagnostic("hook_push_active_skill_data_create_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(
        battle_hud_pause_click_pointer, &hook_battle_hud_pause_click,
        reinterpret_cast<void**>(&g_original_battle_hud_pause_click));
    if (status != MH_OK) {
        diagnostic("hook_battle_hud_pause_click_create_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(context_select_target_pointer, &hook_context_select_target,
                           reinterpret_cast<void**>(&g_original_context_select_target));
    if (status != MH_OK) {
        diagnostic("hook_context_select_target_create_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(create_manual_pointer, &hook_create_manual_command,
                           reinterpret_cast<void**>(&g_original_create_manual_command));
    if (status != MH_OK) {
        diagnostic("hook_create_manual_command_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(acceptable_targets_pointer, &hook_get_acceptable_targets,
                           reinterpret_cast<void**>(
                               &g_original_get_acceptable_targets));
    if (status != MH_OK) {
        diagnostic("hook_get_acceptable_targets_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(
        selection_enabled_pointer, &hook_selection_on_enabled,
        reinterpret_cast<void**>(&g_original_selection_on_enabled));
    if (status != MH_OK) {
        diagnostic("hook_selection_enabled_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(
        selection_disabled_pointer, &hook_selection_on_disabled,
        reinterpret_cast<void**>(&g_original_selection_on_disabled));
    if (status != MH_OK) {
        diagnostic("hook_selection_disabled_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(
        selection_refresh_pointer, &hook_selection_refresh,
        reinterpret_cast<void**>(&g_original_selection_refresh));
    if (status != MH_OK) {
        diagnostic("hook_selection_refresh_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(
        selection_start_pointer, &hook_selection_start_battle_click,
        reinterpret_cast<void**>(&g_original_selection_start_battle_click));
    if (status != MH_OK) {
        diagnostic("hook_selection_start_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    status = MH_CreateHook(
        result_total_damage_pointer, &hook_result_total_damage,
        reinterpret_cast<void**>(&g_original_result_total_damage));
    if (status != MH_OK) {
        diagnostic("hook_result_total_damage_failed status=" +
                   std::to_string(status));
        MH_Uninitialize();
        return false;
    }

    MH_QueueEnableHook(enabled_pointer);
    MH_QueueEnableHook(disabled_pointer);
    MH_QueueEnableHook(request_pointer);
    MH_QueueEnableHook(select_skill_pointer);
    MH_QueueEnableHook(select_target_pointer);
    MH_QueueEnableHook(mode_select_skill_pointer);
    MH_QueueEnableHook(push_active_skill_data_pointer);
    MH_QueueEnableHook(battle_hud_pause_click_pointer);
    MH_QueueEnableHook(context_select_target_pointer);
    MH_QueueEnableHook(acceptable_targets_pointer);
    MH_QueueEnableHook(create_manual_pointer);
    MH_QueueEnableHook(selection_enabled_pointer);
    MH_QueueEnableHook(selection_disabled_pointer);
    MH_QueueEnableHook(selection_refresh_pointer);
    MH_QueueEnableHook(selection_start_pointer);
    MH_QueueEnableHook(result_total_damage_pointer);
    status = MH_ApplyQueued();
    if (status != MH_OK) {
        diagnostic("hook_enable_failed status=" + std::to_string(status));
        MH_Uninitialize();
        return false;
    }
    g_hooks_installed.store(true, std::memory_order_release);
    diagnostic("capture_hooks_installed");
    return true;
}

bool install_hydra_selection_hooks(
    const MethodLocation& on_enabled,
    const MethodLocation& on_disabled,
    const MethodLocation& refresh,
    const MethodLocation& start_battle_click,
    const MethodLocation& result_total_damage,
    const MethodLocation& damage_counter_change) {
    void* enabled_pointer = native_method_pointer(on_enabled);
    void* disabled_pointer = native_method_pointer(on_disabled);
    void* refresh_pointer = native_method_pointer(refresh);
    void* start_pointer = native_method_pointer(start_battle_click);
    void* result_pointer = native_method_pointer(result_total_damage);
    void* damage_counter_pointer = native_method_pointer(damage_counter_change);
    if (!enabled_pointer || !disabled_pointer || !refresh_pointer) {
        diagnostic("hydra_selection_hook_target_missing");
        return false;
    }
    struct HookEntry {
        void* target;
        void* detour;
        void** original;
        const char* name;
    };
    const std::array<HookEntry, 6> hooks{{
        {enabled_pointer,
         reinterpret_cast<void*>(&hook_hydra_selection_on_enabled),
         reinterpret_cast<void**>(&g_original_hydra_selection_on_enabled),
         "enabled"},
        {disabled_pointer,
         reinterpret_cast<void*>(&hook_hydra_selection_on_disabled),
         reinterpret_cast<void**>(&g_original_hydra_selection_on_disabled),
         "disabled"},
        {refresh_pointer,
         reinterpret_cast<void*>(&hook_hydra_selection_refresh),
         reinterpret_cast<void**>(&g_original_hydra_selection_refresh),
         "refresh"},
        {start_pointer,
         reinterpret_cast<void*>(&hook_hydra_selection_start_battle_click),
         reinterpret_cast<void**>(
             &g_original_hydra_selection_start_battle_click),
         "start"},
        {result_pointer,
         reinterpret_cast<void*>(&hook_hydra_result_total_damage),
         reinterpret_cast<void**>(&g_original_hydra_result_total_damage),
         "result"},
        {damage_counter_pointer,
         reinterpret_cast<void*>(&hook_hydra_damage_counter_change),
         reinterpret_cast<void**>(&g_original_hydra_damage_counter_change),
         "damage_counter"},
    }};
    for (const HookEntry& hook : hooks) {
        if (!hook.target) {
            diagnostic(std::string("hydra_selection_optional_hook_missing name=") +
                       hook.name);
            continue;
        }
        const MH_STATUS create_status = MH_CreateHook(
            hook.target, hook.detour, hook.original);
        if (create_status != MH_OK) {
            diagnostic(std::string("hydra_selection_hook_create_failed name=") +
                       hook.name + " status=" +
                       std::to_string(create_status));
            return false;
        }
        const MH_STATUS enable_status = MH_EnableHook(hook.target);
        if (enable_status != MH_OK) {
            diagnostic(std::string("hydra_selection_hook_enable_failed name=") +
                       hook.name + " status=" +
                       std::to_string(enable_status));
            return false;
        }
        if (hook.target == damage_counter_pointer) {
            g_hydra_damage_counter_hook_installed.store(
                true, std::memory_order_release);
        }
    }
    diagnostic("hydra_selection_hooks_installed");
    return true;
}

bool is_raid_process() {
    std::array<wchar_t, 32768> path{};
    const DWORD length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!length || length >= path.size()) {
        return false;
    }
    const wchar_t* file_name = std::wcsrchr(path.data(), L'\\');
    file_name = file_name ? file_name + 1 : path.data();
    return _wcsicmp(file_name, L"Raid.exe") == 0;
}

const Il2CppImage* find_image(Il2CppApi& api, const char* wanted) {
    Il2CppDomain* domain = api.domain_get();
    if (!domain) {
        return nullptr;
    }

    std::size_t count = 0;
    const Il2CppAssembly** assemblies = api.domain_get_assemblies(domain, &count);
    for (std::size_t index = 0; assemblies && index < count; ++index) {
        const Il2CppImage* image = api.assembly_get_image(assemblies[index]);
        const char* name = image ? api.image_get_name(image) : nullptr;
        if (name && std::strcmp(name, wanted) == 0) {
            return image;
        }
    }
    return nullptr;
}

ClassLocation find_class_anywhere(Il2CppApi& api, const char* wanted) {
    Il2CppDomain* domain = api.domain_get();
    if (!domain) {
        return {};
    }
    std::size_t assembly_count = 0;
    const Il2CppAssembly** assemblies =
        api.domain_get_assemblies(domain, &assembly_count);
    for (std::size_t assembly_index = 0;
         assemblies && assembly_index < assembly_count; ++assembly_index) {
        const Il2CppImage* image = api.assembly_get_image(assemblies[assembly_index]);
        if (!image) {
            continue;
        }
        const char* image_name = api.image_get_name(image);
        const std::size_t class_count = api.image_get_class_count(image);
        for (std::size_t class_index = 0; class_index < class_count; ++class_index) {
            Il2CppClass* klass = api.image_get_class(image, class_index);
            const char* class_name = klass ? api.class_get_name(klass) : nullptr;
            if (class_name && std::strcmp(class_name, wanted) == 0) {
                const char* name_space = api.class_get_namespace(klass);
                return {klass, image_name ? image_name : "", name_space ? name_space : ""};
            }
        }
    }
    return {};
}

ClassLocation find_class_in_namespace(Il2CppApi& api, const char* wanted_namespace,
                                      const char* wanted_name) {
    Il2CppDomain* domain = api.domain_get();
    if (!domain) {
        return {};
    }
    std::size_t assembly_count = 0;
    const Il2CppAssembly** assemblies =
        api.domain_get_assemblies(domain, &assembly_count);
    for (std::size_t assembly_index = 0;
         assemblies && assembly_index < assembly_count; ++assembly_index) {
        const Il2CppImage* image = api.assembly_get_image(assemblies[assembly_index]);
        if (!image) {
            continue;
        }
        Il2CppClass* klass =
            api.class_from_name(image, wanted_namespace, wanted_name);
        if (klass) {
            const char* image_name = api.image_get_name(image);
            return {klass, image_name ? image_name : "", wanted_namespace};
        }
    }
    return {};
}

MethodLocation find_method_by_name(Il2CppApi& api, Il2CppClass* klass,
                                   const char* wanted) {
    for (int depth = 0; klass && depth < 32; ++depth) {
        void* iterator = nullptr;
        while (const MethodInfo* method = api.class_get_methods(klass, &iterator)) {
            const char* name = api.method_get_name(method);
            if (name && std::strcmp(name, wanted) == 0) {
                const char* class_name = api.class_get_name(klass);
                return {method, api.method_get_param_count(method),
                        class_name ? class_name : ""};
            }
        }
        klass = api.class_get_parent(klass);
    }
    return {};
}

bool has_class(Il2CppApi& api, const char* image_name, const char* name_space,
               const char* class_name) {
    const Il2CppImage* image = find_image(api, image_name);
    return image && api.class_from_name(image, name_space, class_name);
}

bool has_method(Il2CppApi& api, const char* image_name, const char* name_space,
                const char* class_name, const char* method_name, int argument_count) {
    const Il2CppImage* image = find_image(api, image_name);
    if (!image) {
        return false;
    }
    Il2CppClass* klass = api.class_from_name(image, name_space, class_name);
    return klass && api.class_get_method_from_name(klass, method_name, argument_count);
}

void append_bool(std::ostringstream& output, const char* key, bool value, bool comma = true) {
    output << '\"' << key << "\":" << (value ? "true" : "false");
    if (comma) {
        output << ',';
    }
}

std::string json_escape(const std::string& value) {
    std::string escaped;
    constexpr char hex[] = "0123456789abcdef";
    for (const unsigned char character : value) {
        switch (character) {
        case '\\':
            escaped += "\\\\";
            break;
        case '"':
            escaped += "\\\"";
            break;
        case '\b':
            escaped += "\\b";
            break;
        case '\f':
            escaped += "\\f";
            break;
        case '\n':
            escaped += "\\n";
            break;
        case '\r':
            escaped += "\\r";
            break;
        case '\t':
            escaped += "\\t";
            break;
        default:
            if (character < 0x20) {
                escaped += "\\u00";
                escaped.push_back(hex[character >> 4]);
                escaped.push_back(hex[character & 0x0f]);
            } else {
                escaped.push_back(static_cast<char>(character));
            }
            break;
        }
    }
    return escaped;
}

void append_class_location(std::ostringstream& output, const char* key,
                           const ClassLocation& location, bool comma = true) {
    output << '\"' << key << "\":{\"found\":"
           << (location.klass ? "true" : "false")
           << ",\"image\":\"" << json_escape(location.image)
           << "\",\"namespace\":\"" << json_escape(location.name_space) << "\"}";
    if (comma) {
        output << ',';
    }
}

void append_method_location(std::ostringstream& output, const char* key,
                            const MethodLocation& location, bool comma = true) {
    output << '\"' << key << "\":{\"found\":"
           << (location.method ? "true" : "false")
           << ",\"parameterCount\":" << location.parameter_count
           << ",\"declaringClass\":\"" << json_escape(location.declaring_class)
           << "\"}";
    if (comma) {
        output << ',';
    }
}

void append_class_members(std::ostringstream& output, Il2CppApi& api,
                          const ClassLocation& location) {
    output << '[';
    Il2CppClass* klass = location.klass;
    for (int depth = 0; klass && depth < 4; ++depth) {
        if (depth) {
            output << ',';
        }
        const char* class_name = api.class_get_name(klass);
        output << "{\"class\":\"" << json_escape(class_name ? class_name : "")
               << "\",\"methods\":[";

        bool first = true;
        void* method_iterator = nullptr;
        int method_count = 0;
        while (method_count < 256) {
            const MethodInfo* method = api.class_get_methods(klass, &method_iterator);
            if (!method) {
                break;
            }
            const char* method_name = api.method_get_name(method);
            if (!first) {
                output << ',';
            }
            first = false;
            const std::uint32_t parameter_count = api.method_get_param_count(method);
            char* return_type_name =
                api.type_get_name(api.method_get_return_type(method));
            output << "{\"name\":\"" << json_escape(method_name ? method_name : "")
                   << "\",\"parameterCount\":" << parameter_count
                   << ",\"returnType\":\""
                   << json_escape(return_type_name ? return_type_name : "")
                   << "\",\"parameters\":[";
            if (return_type_name) {
                api.free(return_type_name);
            }
            for (std::uint32_t parameter_index = 0;
                 parameter_index < parameter_count; ++parameter_index) {
                if (parameter_index) {
                    output << ',';
                }
                const char* parameter_name =
                    api.method_get_param_name(method, parameter_index);
                char* parameter_type_name =
                    api.type_get_name(api.method_get_param(method, parameter_index));
                output << "{\"name\":\""
                       << json_escape(parameter_name ? parameter_name : "")
                       << "\",\"type\":\""
                       << json_escape(parameter_type_name ? parameter_type_name : "")
                       << "\"}";
                if (parameter_type_name) {
                    api.free(parameter_type_name);
                }
            }
            output << "]}";
            ++method_count;
        }

        output << "],\"fields\":[";
        first = true;
        void* field_iterator = nullptr;
        int field_count = 0;
        while (field_count < 256) {
            FieldInfo* field = api.class_get_fields(klass, &field_iterator);
            if (!field) {
                break;
            }
            const char* field_name = api.field_get_name(field);
            char* field_type_name = api.type_get_name(api.field_get_type(field));
            if (!first) {
                output << ',';
            }
            first = false;
            output << "{\"name\":\"" << json_escape(field_name ? field_name : "")
                   << "\",\"offset\":" << api.field_get_offset(field)
                   << ",\"type\":\""
                   << json_escape(field_type_name ? field_type_name : "") << "\"}";
            if (field_type_name) {
                api.free(field_type_name);
            }
            ++field_count;
        }

        output << "],\"properties\":[";
        first = true;
        void* property_iterator = nullptr;
        int property_count = 0;
        while (property_count < 128) {
            const PropertyInfo* property =
                api.class_get_properties(klass, &property_iterator);
            if (!property) {
                break;
            }
            const char* property_name = api.property_get_name(property);
            const MethodInfo* getter = api.property_get_get_method(property);
            if (!first) {
                output << ',';
            }
            first = false;
            output << "{\"name\":\""
                   << json_escape(property_name ? property_name : "")
                   << "\",\"hasGetter\":" << (getter ? "true" : "false") << '}';
            ++property_count;
        }
        output << "]}";
        klass = api.class_get_parent(klass);
    }
    output << ']';
}

struct AccountIdentity {
    bool app_model_ready{};
    bool user_wrapper_ready{};
    bool user_ready{};
    bool social_data_ready{};
    std::int64_t user_id{};
    std::string account_name;
};

AccountIdentity read_account_identity(void* app_model_instance) {
    AccountIdentity identity{};
    void* user_wrapper = nullptr;
    void* user = nullptr;
    void* social_data = nullptr;
    void* game_data = nullptr;
    void* game_settings = nullptr;
    identity.app_model_ready = app_model_instance != nullptr;
    identity.user_wrapper_ready =
        identity.app_model_ready &&
        safe_read_field(app_model_instance, "_userWrapper", nullptr,
                        user_wrapper) &&
        user_wrapper;
    identity.user_ready =
        identity.user_wrapper_ready &&
        safe_read_field(user_wrapper, "User", nullptr, user) && user;
    if (identity.user_ready) {
        safe_read_field(user, "Id", nullptr, identity.user_id);
        identity.social_data_ready =
            safe_read_field(user, "SocialData", nullptr, social_data) &&
            social_data;
        if (safe_read_field(user, "GameData", nullptr, game_data) && game_data) {
            safe_read_field(game_data, "UserGameSettings", nullptr,
                            game_settings);
        }
    }
    if (game_settings) {
        void* name = nullptr;
        safe_read_field(game_settings, "Name", nullptr, name);
        identity.account_name = il2cpp_string_utf8(name);
    }
    return identity;
}

void append_account_model_snapshot(std::ostringstream& output,
                                   void* app_model_instance) {
    const AccountIdentity identity = read_account_identity(app_model_instance);
    output << "{\"appModelReady\":"
           << (identity.app_model_ready ? "true" : "false")
           << ",\"userWrapperReady\":"
           << (identity.user_wrapper_ready ? "true" : "false")
           << ",\"userReady\":" << (identity.user_ready ? "true" : "false")
           << ",\"userId\":";
    if (identity.user_id > 0) {
        output << identity.user_id;
    } else {
        output << "null";
    }
    output << ",\"socialDataReady\":"
           << (identity.social_data_ready ? "true" : "false")
           << ",\"accountName\":\""
           << json_escape(identity.account_name) << "\"}";
}

void log_account_identity(void* app_model_instance) {
    void* latest_app_model = refresh_app_model_instance();
    if (latest_app_model) {
        app_model_instance = latest_app_model;
    }
    const AccountIdentity identity = read_account_identity(app_model_instance);
    std::ostringstream output;
    output << "{\"type\":\"account_state\",\"pid\":"
           << GetCurrentProcessId() << ",\"userId\":";
    if (identity.user_id > 0) {
        output << identity.user_id;
    } else {
        output << "null";
    }
    output << ",\"accountName\":\""
           << json_escape(identity.account_name) << "\"}";
    if (g_shared_state) {
        publish_shared_json(g_shared_state->account, output.str());
    }
    diagnostic("account_state_published user_id_available=" +
               std::to_string(identity.user_id > 0 ? 1 : 0));
}

bool validate_takeover_account() {
    const std::uint64_t expected_user_id =
        g_takeover_user_id.load(std::memory_order_acquire);
    void* app_model_instance = refresh_app_model_instance();
    const AccountIdentity current = read_account_identity(app_model_instance);
    if (!expected_user_id || current.user_id <= 0 ||
        static_cast<std::uint64_t>(current.user_id) != expected_user_id) {
        log_account_identity(app_model_instance);
        interrupt_takeover("account_changed", "account_guard");
        diagnostic("account_guard_rejected expected_user_id=" +
                   std::to_string(expected_user_id) + " actual_user_id=" +
                   std::to_string(current.user_id));
        return false;
    }
    return true;
}

bool is_account_name_field(const char* field_name) {
    if (!field_name) {
        return false;
    }
    constexpr std::array<const char*, 12> candidates = {
        "Name", "UserName", "Username", "DisplayName", "Nickname", "NickName",
        "PlayerName", "SocialName", "<Name>k__BackingField",
        "<UserName>k__BackingField", "<DisplayName>k__BackingField",
        "<PlayerName>k__BackingField"};
    for (const char* candidate : candidates) {
        if (std::strcmp(field_name, candidate) == 0) {
            return true;
        }
    }
    return false;
}

void append_account_name_source(std::ostringstream& output, Il2CppApi& api,
                                const char* source, void* object,
                                bool& first_source) {
    if (!object) {
        return;
    }
    Il2CppClass* klass = nullptr;
    if (!safe_read(object, 0, klass) || !klass) {
        return;
    }
    std::ostringstream fields;
    bool first_field = true;
    for (int depth = 0; klass && depth < 6; ++depth) {
        void* iterator = nullptr;
        while (FieldInfo* field = api.class_get_fields(klass, &iterator)) {
            const char* field_name = api.field_get_name(field);
            char* type_name = api.type_get_name(api.field_get_type(field));
            const std::int32_t offset = api.field_get_offset(field);
            const bool selected =
                offset >= 16 && is_account_name_field(field_name) && type_name &&
                std::strcmp(type_name, "System.String") == 0;
            if (selected) {
                void* value = nullptr;
                safe_read(object, static_cast<std::size_t>(offset), value);
                if (!first_field) {
                    fields << ',';
                }
                first_field = false;
                fields << "{\"field\":\"" << json_escape(field_name)
                       << "\",\"value\":\""
                       << json_escape(il2cpp_string_utf8(value)) << "\"}";
            }
            if (type_name) {
                api.free(type_name);
            }
        }
        klass = api.class_get_parent(klass);
    }
    if (first_field) {
        return;
    }
    if (!first_source) {
        output << ',';
    }
    first_source = false;
    output << "{\"source\":\"" << json_escape(source) << "\",\"fields\":["
           << fields.str() << "]}";
}

void append_account_name_candidates(std::ostringstream& output, Il2CppApi& api,
                                    void* app_model_instance) {
    void* user_wrapper = nullptr;
    void* user = nullptr;
    void* game_data = nullptr;
    void* account_data = nullptr;
    void* raid_show_data = nullptr;
    void* game_settings = nullptr;
    void* account_wrapper = nullptr;
    void* shared_account_data = nullptr;
    if (app_model_instance) {
        safe_read_field(app_model_instance, "_userWrapper", nullptr,
                        user_wrapper);
    }
    if (user_wrapper) {
        safe_read_field(user_wrapper, "User", nullptr, user);
        safe_read_field(user_wrapper, "Account", nullptr, account_wrapper);
    }
    if (user) {
        safe_read_field(user, "GameData", nullptr, game_data);
    }
    if (game_data) {
        safe_read_field(game_data, "UserGameSettings", nullptr, game_settings);
        safe_read_field(game_data, "Account", nullptr, account_data);
        safe_read_field(game_data, "RaidShowData", nullptr, raid_show_data);
    }
    if (account_wrapper) {
        safe_read_field(account_wrapper, "AccountData", nullptr,
                        shared_account_data);
    }

    output << '[';
    bool first_source = true;
    append_account_name_source(output, api, "UserGameData.UserGameSettings",
                               game_settings, first_source);
    append_account_name_source(output, api, "UserGameData.Account",
                               account_data, first_source);
    append_account_name_source(output, api, "UserGameData.RaidShowData",
                               raid_show_data, first_source);
    append_account_name_source(output, api, "AccountWrapper.AccountData",
                               shared_account_data, first_source);
    output << ']';
}

void append_localizer_field_references(std::ostringstream& output,
                                       Il2CppApi& api) {
    output << '[';
    Il2CppDomain* domain = api.domain_get();
    std::size_t assembly_count = 0;
    const Il2CppAssembly** assemblies =
        domain ? api.domain_get_assemblies(domain, &assembly_count) : nullptr;
    bool first = true;
    std::size_t match_count = 0;
    for (std::size_t assembly_index = 0;
         assemblies && assembly_index < assembly_count && match_count < 256;
         ++assembly_index) {
        const Il2CppImage* image = api.assembly_get_image(assemblies[assembly_index]);
        if (!image) {
            continue;
        }
        const char* image_name = api.image_get_name(image);
        const std::size_t class_count = api.image_get_class_count(image);
        for (std::size_t class_index = 0;
             class_index < class_count && match_count < 256; ++class_index) {
            Il2CppClass* klass = api.image_get_class(image, class_index);
            if (!klass) {
                continue;
            }
            void* field_iterator = nullptr;
            while (FieldInfo* field = api.class_get_fields(klass, &field_iterator)) {
                char* field_type_name = api.type_get_name(api.field_get_type(field));
                const bool is_match = field_type_name &&
                    (std::strstr(field_type_name, "ILocalizerService") ||
                     std::strcmp(field_type_name,
                                 "Client.Model.Common.Localization.Localizer") == 0);
                if (!is_match) {
                    if (field_type_name) {
                        api.free(field_type_name);
                    }
                    continue;
                }

                const std::uint32_t flags = api.field_get_flags(field);
                constexpr std::uint32_t kFieldAttributeStatic = 0x0010;
                const bool is_static = (flags & kFieldAttributeStatic) != 0;
                void* static_value = nullptr;
                if (is_static) {
                    safe_static_field_value(field, static_value);
                }
                const char* class_name = api.class_get_name(klass);
                const char* name_space = api.class_get_namespace(klass);
                const char* field_name = api.field_get_name(field);
                if (!first) {
                    output << ',';
                }
                first = false;
                output << "{\"image\":\""
                       << json_escape(image_name ? image_name : "")
                       << "\",\"namespace\":\""
                       << json_escape(name_space ? name_space : "")
                       << "\",\"class\":\""
                       << json_escape(class_name ? class_name : "")
                       << "\",\"field\":\""
                       << json_escape(field_name ? field_name : "")
                       << "\",\"fieldType\":\""
                       << json_escape(field_type_name)
                       << "\",\"offset\":" << api.field_get_offset(field)
                       << ",\"flags\":" << flags
                       << ",\"static\":" << (is_static ? "true" : "false")
                       << ",\"staticValue\":"
                       << reinterpret_cast<std::uintptr_t>(static_value) << '}';
                api.free(field_type_name);
                ++match_count;
                if (match_count >= 256) {
                    break;
                }
            }
        }
    }
    output << ']';
}

void append_type_references(std::ostringstream& output, Il2CppApi& api,
                            const char* needle) {
    output << '[';
    Il2CppDomain* domain = api.domain_get();
    std::size_t assembly_count = 0;
    const Il2CppAssembly** assemblies =
        domain ? api.domain_get_assemblies(domain, &assembly_count) : nullptr;
    bool first = true;
    std::size_t match_count = 0;
    auto append_match = [&](const Il2CppImage* image, Il2CppClass* klass,
                            const char* kind, const char* member,
                            const char* type_name) {
        if (!first) {
            output << ',';
        }
        first = false;
        const char* image_name = api.image_get_name(image);
        const char* class_name = api.class_get_name(klass);
        const char* name_space = api.class_get_namespace(klass);
        output << "{\"image\":\""
               << json_escape(image_name ? image_name : "")
               << "\",\"namespace\":\""
               << json_escape(name_space ? name_space : "")
               << "\",\"class\":\""
               << json_escape(class_name ? class_name : "")
               << "\",\"kind\":\"" << kind
               << "\",\"member\":\""
               << json_escape(member ? member : "")
               << "\",\"type\":\""
               << json_escape(type_name ? type_name : "") << "\"}";
        ++match_count;
    };

    for (std::size_t assembly_index = 0;
         assemblies && assembly_index < assembly_count && match_count < 256;
         ++assembly_index) {
        const Il2CppImage* image = api.assembly_get_image(assemblies[assembly_index]);
        if (!image) {
            continue;
        }
        const std::size_t class_count = api.image_get_class_count(image);
        for (std::size_t class_index = 0;
             class_index < class_count && match_count < 256; ++class_index) {
            Il2CppClass* klass = api.image_get_class(image, class_index);
            if (!klass) {
                continue;
            }
            void* field_iterator = nullptr;
            while (FieldInfo* field = api.class_get_fields(klass, &field_iterator)) {
                char* type_name = api.type_get_name(api.field_get_type(field));
                if (type_name && std::strstr(type_name, needle)) {
                    append_match(image, klass, "field", api.field_get_name(field),
                                 type_name);
                }
                if (type_name) {
                    api.free(type_name);
                }
                if (match_count >= 256) {
                    break;
                }
            }
            void* method_iterator = nullptr;
            while (match_count < 256) {
                const MethodInfo* method =
                    api.class_get_methods(klass, &method_iterator);
                if (!method) {
                    break;
                }
                const char* method_name = api.method_get_name(method);
                char* return_type =
                    api.type_get_name(api.method_get_return_type(method));
                if (return_type && std::strstr(return_type, needle)) {
                    append_match(image, klass, "return", method_name,
                                 return_type);
                }
                if (return_type) {
                    api.free(return_type);
                }
                const std::uint32_t parameter_count =
                    api.method_get_param_count(method);
                for (std::uint32_t parameter_index = 0;
                     parameter_index < parameter_count && match_count < 256;
                     ++parameter_index) {
                    char* parameter_type = api.type_get_name(
                        api.method_get_param(method, parameter_index));
                    if (parameter_type && std::strstr(parameter_type, needle)) {
                        append_match(image, klass, "parameter", method_name,
                                     parameter_type);
                    }
                    if (parameter_type) {
                        api.free(parameter_type);
                    }
                }
            }
        }
    }
    output << ']';
}

bool is_account_name_member(const char* name) {
    if (!name) {
        return false;
    }
    constexpr std::array<const char*, 27> candidates = {
        "Name", "UserName", "Username", "DisplayName", "Nickname", "NickName",
        "PlayerName", "<Name>k__BackingField", "<UserName>k__BackingField",
        "<Username>k__BackingField", "<DisplayName>k__BackingField",
        "<Nickname>k__BackingField", "<NickName>k__BackingField",
        "<PlayerName>k__BackingField", "get_Name", "get_UserName",
        "get_Username", "get_DisplayName", "get_Nickname", "get_NickName",
        "get_PlayerName", "set_Name", "set_UserName", "set_Username",
        "set_DisplayName", "set_Nickname", "set_PlayerName"};
    for (const char* candidate : candidates) {
        if (std::strcmp(name, candidate) == 0) {
            return true;
        }
    }
    return false;
}

void append_account_name_member_references(std::ostringstream& output,
                                           Il2CppApi& api) {
    output << '[';
    Il2CppDomain* domain = api.domain_get();
    std::size_t assembly_count = 0;
    const Il2CppAssembly** assemblies =
        domain ? api.domain_get_assemblies(domain, &assembly_count) : nullptr;
    bool first = true;
    std::size_t match_count = 0;
    auto append_match = [&](const Il2CppImage* image, Il2CppClass* klass,
                            const char* kind, const char* member,
                            const char* type_name) {
        if (!first) {
            output << ',';
        }
        first = false;
        const char* image_name = api.image_get_name(image);
        const char* class_name = api.class_get_name(klass);
        const char* name_space = api.class_get_namespace(klass);
        output << "{\"image\":\""
               << json_escape(image_name ? image_name : "")
               << "\",\"namespace\":\""
               << json_escape(name_space ? name_space : "")
               << "\",\"class\":\""
               << json_escape(class_name ? class_name : "")
               << "\",\"kind\":\"" << kind << "\",\"member\":\""
               << json_escape(member ? member : "") << "\",\"type\":\""
               << json_escape(type_name ? type_name : "") << "\"}";
        ++match_count;
    };
    for (std::size_t assembly_index = 0;
         assemblies && assembly_index < assembly_count && match_count < 512;
         ++assembly_index) {
        const Il2CppImage* image = api.assembly_get_image(assemblies[assembly_index]);
        if (!image) {
            continue;
        }
        const std::size_t class_count = api.image_get_class_count(image);
        for (std::size_t class_index = 0;
             class_index < class_count && match_count < 512; ++class_index) {
            Il2CppClass* klass = api.image_get_class(image, class_index);
            if (!klass) {
                continue;
            }
            void* field_iterator = nullptr;
            while (FieldInfo* field = api.class_get_fields(klass, &field_iterator)) {
                const char* field_name = api.field_get_name(field);
                if (!is_account_name_member(field_name)) {
                    continue;
                }
                char* type_name = api.type_get_name(api.field_get_type(field));
                append_match(image, klass, "field", field_name, type_name);
                if (type_name) {
                    api.free(type_name);
                }
                if (match_count >= 512) {
                    break;
                }
            }
            void* method_iterator = nullptr;
            while (match_count < 512) {
                const MethodInfo* method =
                    api.class_get_methods(klass, &method_iterator);
                if (!method) {
                    break;
                }
                const char* method_name = api.method_get_name(method);
                if (!is_account_name_member(method_name)) {
                    continue;
                }
                char* return_type =
                    api.type_get_name(api.method_get_return_type(method));
                append_match(image, klass, "method", method_name, return_type);
                if (return_type) {
                    api.free(return_type);
                }
            }
        }
    }
    output << ']';
}

void* read_service_locator_localizer(Il2CppApi& api,
                                     const ClassLocation& service_locator) {
    if (!service_locator.klass) {
        return nullptr;
    }
    void* iterator = nullptr;
    while (FieldInfo* field = api.class_get_fields(service_locator.klass, &iterator)) {
        const char* name = api.field_get_name(field);
        if (name && std::strcmp(name, "<Localizer>k__BackingField") == 0) {
            void* value = nullptr;
            return safe_static_field_value(field, value) ? value : nullptr;
        }
    }
    return nullptr;
}

void append_localization_test(std::ostringstream& output, Il2CppApi& api,
                              const ClassLocation& localizer,
                              const ClassLocation& service_locator,
                              const char* key) {
    void* instance = read_service_locator_localizer(api, service_locator);
    const MethodInfo* method = localizer.klass
        ? api.class_get_method_from_name(localizer.klass, "Localize", 2)
        : nullptr;
    output << "{\"key\":\"" << json_escape(key ? key : "")
           << "\",\"instance\":" << reinterpret_cast<std::uintptr_t>(instance)
           << ",\"methodFound\":" << (method ? "true" : "false")
           << ",\"results\":[";
    bool first = true;
    for (std::int32_t priority = 0;
         instance && method && key && priority < 8; ++priority) {
        Il2CppString* managed_key = api.string_new(key);
        void* parameters[2] = {managed_key, &priority};
        void* exception = nullptr;
        void* result = api.runtime_invoke(method, instance, parameters, &exception);
        if (!first) {
            output << ',';
        }
        first = false;
        output << "{\"priority\":" << priority
               << ",\"exception\":"
               << reinterpret_cast<std::uintptr_t>(exception)
               << ",\"text\":\""
               << json_escape(exception ? "" : il2cpp_string_utf8(result))
               << "\"}";
    }
    output << "]}";
}

void publish(const std::string& payload) {
    OutputDebugStringA(payload.c_str());
    diagnostic("publish_wait_pipe");
    const std::wstring pipe_name =
        LR"(\\.\pipe\RaidChimeraPrototype-)" +
        std::to_wstring(GetCurrentProcessId());
    if (!WaitNamedPipeW(pipe_name.c_str(), 1000)) {
        diagnostic("publish_wait_pipe_failed error=" + std::to_string(GetLastError()));
        return;
    }

    HANDLE pipe = CreateFileW(pipe_name.c_str(), GENERIC_WRITE, 0, nullptr,
                              OPEN_EXISTING, 0, nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
        diagnostic("publish_open_pipe_failed error=" + std::to_string(GetLastError()));
        return;
    }

    DWORD written = 0;
    WriteFile(pipe, payload.data(), static_cast<DWORD>(payload.size()), &written, nullptr);
    CloseHandle(pipe);
    diagnostic("publish_complete bytes=" + std::to_string(written));
}

DWORD WINAPI probe_thread(void*) {
    if (initialize_shared_state()) {
        end_takeover(0, "agent_initialized");
    }
    diagnostic("probe_thread_started");
    HMODULE game_assembly = nullptr;
    for (int attempt = 0; attempt < 600 && !game_assembly; ++attempt) {
        game_assembly = GetModuleHandleW(L"GameAssembly.dll");
        if (!game_assembly) {
            Sleep(100);
        }
    }

    Il2CppApi& api = g_api;
    if (!game_assembly || !api.load(game_assembly)) {
        set_agent_state(kAgentStateFailed);
        diagnostic("il2cpp_exports_missing");
        publish(R"({"type":"probe","ok":false,"reason":"il2cpp_exports_missing"})");
        return 0;
    }
    diagnostic("il2cpp_exports_resolved");

    Il2CppDomain* domain = nullptr;
    for (int attempt = 0; attempt < 600 && !domain; ++attempt) {
        domain = api.domain_get();
        if (!domain) {
            Sleep(100);
        }
    }
    if (!domain) {
        set_agent_state(kAgentStateFailed);
        diagnostic("il2cpp_domain_unavailable");
        publish(R"({"type":"probe","ok":false,"reason":"domain_unavailable"})");
        return 0;
    }
    diagnostic("il2cpp_domain_ready");

    void* attached_thread = api.thread_attach(domain);
    diagnostic(attached_thread ? "il2cpp_thread_attached" : "il2cpp_thread_attach_failed");

    const ClassLocation battle_processor = find_class_anywhere(api, "BattleProcessor");
    const ClassLocation battle_context = find_class_anywhere(api, "ClientBattleViewContext");
    const ClassLocation command_generator = find_class_anywhere(api, "ClientCommandGenerator");
    const ClassLocation client_battle_mode = find_class_anywhere(api, "ClientBattleMode");
    const ClassLocation live_battle_mode = find_class_anywhere(api, "ClientLiveBattleMode");
    const ClassLocation challenge_context = find_class_anywhere(api, "ChimeraChallengeContext");
    const ClassLocation static_alliance_data =
        find_class_anywhere(api, "StaticAllianceData");
    const ClassLocation chimera_challenge_type =
        find_class_anywhere(api, "ChimeraChallengeType");
    const ClassLocation chimera_challenge_rewards =
        find_class_anywhere(api, "ChimeraChallengeRewards");
    const ClassLocation chimera_challenge_type_container =
        find_class_anywhere(api, "ChimeraChallengeTypeContainer");
    const ClassLocation chimera_challenge_name_combiner =
        find_class_anywhere(api, "ChimeraChallengeNameCombiner");
    const ClassLocation chimera_challenges_overlay =
        find_class_anywhere(api, "ChimeraChallengesOverlayContext");
    const ClassLocation challenges_by_chimera_form =
        find_class_anywhere(api, "ChallengesByChimeraChallengeFormContext");
    const ClassLocation challenges_by_chimera_part =
        find_class_anywhere(api, "ChallengesByChimeraChallengePartContext");
    const ClassLocation chimera_challenge_dialog =
        find_class_anywhere(api, "ChimeraChallengeDialogContext");
    const ClassLocation chimera_challenge_form_context =
        find_class_anywhere(api, "ChimeraChallengeFormContext");
    const ClassLocation chimera_challenge_extensions =
        find_class_anywhere(api, "ChimeraChallengeExtensions");
    const ClassLocation alliance_chimera_type =
        find_class_anywhere(api, "AllianceChimeraType");
    const ClassLocation flexible_reward =
        find_class_anywhere(api, "FlexibleReward");
    const ClassLocation flexible_reward_info =
        find_class_anywhere(api, "FlexibleRewardInfo");
    const ClassLocation flexible_reward_type =
        find_class_anywhere(api, "FlexibleRewardType");
    const ClassLocation alliance_chimera_difficulty = find_class_in_namespace(
        api, "SharedModel.Meta.Alliances.Chimera.Enums",
        "AllianceChimeraDifficulty");
    const ClassLocation chimera_challenge_part = find_class_in_namespace(
        api, "SharedModel.Meta.Alliances.Chimera.Enums",
        "ChimeraChallengePart");
    const ClassLocation chimera_challenge_difficulty = find_class_in_namespace(
        api, "SharedModel.Meta.Alliances.Chimera.Enums",
        "ChimeraChallengeDifficulty");
    const ClassLocation status_effect_type_id = find_class_in_namespace(
        api, "SharedModel.Battle.Effects", "StatusEffectTypeId");
    const ClassLocation resource_type_id = find_class_in_namespace(
        api, "SharedModel.Meta.Account", "ResourceTypeId");
    const ClassLocation battle_statistics = find_class_in_namespace(
        api, "SharedModel.Battle.Core", "BattleStatistics");
    const ClassLocation chimera_multiplier_statistics =
        find_class_anywhere(api, "ChimeraMultiplierStatistics");
    const ClassLocation long_fixed =
        find_class_anywhere(api, "LongFixed");
    const ClassLocation finish_battle_info =
        find_class_anywhere(api, "FinishBattleInfo");
    const ClassLocation resources = find_class_in_namespace(
        api, "SharedModel.Meta.Account", "Resources");
    const ClassLocation user_prize =
        find_class_anywhere(api, "UserPrize");
    const ClassLocation heroes_selection_chimera =
        find_class_anywhere(api, "HeroesSelectionChimeraDialogContext");
    const ClassLocation heroes_selection_hydra =
        find_class_anywhere(api, "HeroesSelectionHydraDialogContext");
    const ClassLocation auto_battle_checkbox =
        find_class_anywhere(api, "AutoBattleCheckboxContext");
    const ClassLocation chimera_quick_battle_checkbox =
        find_class_anywhere(api, "ChimeraQuickBattleCheckboxContext");
    const ClassLocation hydra_quick_battle_checkbox =
        find_class_anywhere(api, "HydraQuickBattleCheckboxContext");
    const ClassLocation hydra_damage_counter = find_class_in_namespace(
        api, "ECS.View.BattleView", "HydraDamageCounter");
    const ClassLocation bool_property =
        find_class_anywhere(api, "BoolProperty");
    const ClassLocation battle_finish_alliance_chimera =
        find_class_anywhere(api, "BattleFinishAllianceChimeraDialogContext");
    const ClassLocation battle_finish_alliance_hydra =
        find_class_anywhere(api, "BattleFinishAllianceHydraDialogContext");
    const ClassLocation battle_finish_alliance_boss =
        find_class_anywhere(api, "BattleFinishAllianceBossDialogContext");
    const ClassLocation alliance_chimera_challenge =
        find_class_anywhere(api, "AllianceChimeraChallengeContext");
    const ClassLocation chimera_challenge_reward =
        find_class_anywhere(api, "ChimeraChallengeRewardContext");
    const ClassLocation completed_challenges_tooltip =
        find_class_anywhere(api, "ChimeraCompletedChallengesTooltipContext");
    const ClassLocation battle_result =
        find_class_anywhere(api, "BattleResult");
    const ClassLocation chimera_form = find_class_anywhere(api, "ChimeraForm");
    const ClassLocation alliance_hydra_type =
        find_class_anywhere(api, "AllianceHydraType");
    const ClassLocation alliance_hydra_difficulty =
        find_class_anywhere(api, "AllianceHydraDifficulty");
    const ClassLocation hydra_head = find_class_anywhere(api, "HydraHead");
    const ClassLocation hydra_neck = find_class_anywhere(api, "HydraNeck");
    const ClassLocation digestion_info =
        find_class_anywhere(api, "DigestionInfo");
    const ClassLocation skill_data = find_class_in_namespace(
        api, "Client.ViewModel.Contextes.BattleHUD", "SkillData");
    const ClassLocation skill_avatar =
        find_class_anywhere(api, "SkillAvatarWithCooldownContext");
    const ClassLocation skill_context = find_class_in_namespace(
        api, "Client.ViewModel.Contextes.BattleHUD", "SkillContext");
    const ClassLocation battle_hud_context = find_class_in_namespace(
        api, "ECS.ViewModel", "BattleHUDContext");
    const ClassLocation hud_state = find_class_in_namespace(
        api, "ECS.ViewModel.BattleHUDContext", "State");
    const ClassLocation actor_ui =
        find_class_anywhere(api, "BattleActorUIContext");
    const ClassLocation actor_with_skills_ui =
        find_class_anywhere(api, "BattleActorUIWithSkillsContext");
    const ClassLocation localizer = find_class_in_namespace(
        api, "Client.Model.Common.Localization", "Localizer");
    const ClassLocation localizer_service = find_class_in_namespace(
        api, "Client.App.Services", "ILocalizerService");
    const ClassLocation service_locator = find_class_in_namespace(
        api, "Client.App.Services", "ServiceLocator");
    const ClassLocation localized_text = find_class_in_namespace(
        api, "Client.Model.Common.Localization", "LocalizedText");
    const ClassLocation localize_utils = find_class_in_namespace(
        api, "Client.ViewModel.Utils", "LocalizeUtils");
    const ClassLocation hero_type = find_class_in_namespace(
        api, "SharedModel.Meta.Heroes", "HeroType");
    const ClassLocation shared_ltext_key = find_class_in_namespace(
        api, "SharedModel.Common.Localization", "SharedLTextKey");
    const ClassLocation skill_type = find_class_in_namespace(
        api, "SharedModel.Meta.Skills", "SkillType");
    const ClassLocation localization_storage = find_class_in_namespace(
        api, "Client.App.Services", "LocalizationStorage");
    const ClassLocation app_model = find_class_in_namespace(
        api, "Client.Model", "AppModel");
    const ClassLocation user_model = find_class_in_namespace(
        api, "Client.Model.Gameplay.User", "User");
    const ClassLocation user_wrapper = find_class_in_namespace(
        api, "Client.Model.Guard", "UserWrapper");
    const ClassLocation user_read_guard = find_class_in_namespace(
        api, "Client.Model.Guard", "UserReadGuard");
    const ClassLocation user_session = find_class_in_namespace(
        api, "Client.Model.Common", "UserSession");
    const ClassLocation user_social_data = find_class_in_namespace(
        api, "Client.Model.Gameplay.Social.Data", "UserSocialData");
    const ClassLocation social_wrapper = find_class_in_namespace(
        api, "Client.Model.Gameplay.Social", "SocialWrapper");
    const ClassLocation user_game_data = find_class_in_namespace(
        api, "Client.Model.Gameplay.User.Data", "UserGameData");
    const ClassLocation account_wrapper = find_class_in_namespace(
        api, "Client.Model.Gameplay.Account", "AccountWrapper");
    const ClassLocation updatable_user_account = find_class_in_namespace(
        api, "Client.Model.Gameplay.Account.Data", "UpdatableUserAccount");
    const ClassLocation shared_user_account = find_class_in_namespace(
        api, "SharedModel.Meta.Account", "UserAccount");
    const ClassLocation updatable_raid_show = find_class_in_namespace(
        api, "Client.Model.Gameplay.RaidShow.Data",
        "UpdatableUserRaidShowData");
    const ClassLocation updatable_game_settings = find_class_in_namespace(
        api, "Client.Model.Gameplay.GameSettings.Data",
        "UpdatableUserGameSettings");
    const ClassLocation shared_game_settings = find_class_in_namespace(
        api, "SharedModel.Meta.Settings", "UserGameSettings");
    const ClassLocation client_static_data = find_class_in_namespace(
        api, "SharedModel.Meta", "ClientStaticData");
    const ClassLocation static_hero_data = find_class_in_namespace(
        api, "SharedModel.Meta.Heroes", "StaticHeroData");
    const ClassLocation static_skill_data = find_class_in_namespace(
        api, "SharedModel.Meta.Skills", "StaticSkillData");
    const ClassLocation chimera_component =
        find_class_anywhere(api, "ChimeraComponent");
    const ClassLocation chimera_difficulty_component =
        find_class_anywhere(api, "ChimeraDifficultyComponent");
    const ClassLocation chimera_hp_state =
        find_class_anywhere(api, "ChimeraHpState");
    const ClassLocation chimera_turn =
        find_class_anywhere(api, "ChimeraTurn");
    const ClassLocation chimera_challenge_turn =
        find_class_anywhere(api, "ChimeraChallengeTurn");
    const ClassLocation battle_context_model = find_class_in_namespace(
        api, "SharedModel.Battle.Core", "BattleContext");
    const ClassLocation battle_state = find_class_in_namespace(
        api, "SharedModel.Battle.Core.State", "BattleState");
    const ClassLocation battle_state_snapshot = find_class_in_namespace(
        api, "SharedModel.Battle.Core.State", "BattleStateSnapshot");
    const ClassLocation hydra_extensions = find_class_in_namespace(
        api, "SharedModel.Battle.Extensions", "HydraExtensions");
    const ClassLocation battle_hero_snapshot = find_class_in_namespace(
        api, "SharedModel.Battle.Core.State", "BattleHeroSnapshot");
    const ClassLocation team_id = find_class_in_namespace(
        api, "SharedModel.Battle.Core.State", "TeamId");
    const ClassLocation battle_hero = find_class_in_namespace(
        api, "SharedModel.Battle.Core.Hero", "BattleHero");
    const ClassLocation battle_team = find_class_in_namespace(
        api, "SharedModel.Battle.Core.Hero", "BattleTeam");
    const ClassLocation fixed_number = find_class_in_namespace(
        api, "Plarium.Common.Numerics", "Fixed");
    const ClassLocation applied_effect = find_class_in_namespace(
        api, "SharedModel.Battle.Core.Skill", "AppliedEffect");
    const ClassLocation battle_skill = find_class_in_namespace(
        api, "SharedModel.Battle.Core.Skill", "BattleSkill");
    const ClassLocation hero_state = find_class_in_namespace(
        api, "SharedModel.Battle.Core.Hero", "HeroState");
    const ClassLocation hero_form = find_class_in_namespace(
        api, "SharedModel.Meta.Heroes", "HeroForm");
    const ClassLocation challenge = find_class_in_namespace(
        api, "SharedModel.Battle.Core.Hero", "Challenge");
    const ClassLocation effect_type = find_class_in_namespace(
        api, "SharedModel.Battle.Effects", "EffectType");
    const ClassLocation static_effect_data = find_class_in_namespace(
        api, "SharedModel.Battle.Effects", "StaticEffectData");
    const ClassLocation effect_kind_id = find_class_in_namespace(
        api, "SharedModel.Battle.Effects", "EffectKindId");
    g_alliance_chimera_difficulty_class =
        alliance_chimera_difficulty.klass;
    g_effect_kind_id_class = effect_kind_id.klass;
    g_chimera_form_class = chimera_form.klass;
    g_chimera_challenge_part_class = chimera_challenge_part.klass;
    g_chimera_challenge_difficulty_class =
        chimera_challenge_difficulty.klass;
    g_status_effect_type_id_class = status_effect_type_id.klass;
    g_flexible_reward_type_class = flexible_reward_type.klass;
    g_resource_type_id_class = resource_type_id.klass;
    const MethodLocation app_model_instance_method =
        find_method_by_name(api, app_model.klass, "get_Instance");
    const MethodLocation app_model_static_data_method =
        find_method_by_name(api, app_model.klass, "get_StaticData");
    const MethodLocation app_model_read_user_method =
        find_method_by_name(api, app_model.klass, "ReadUser");
    const MethodLocation user_read_guard_dispose_method =
        find_method_by_name(api, user_read_guard.klass, "Dispose");
    g_app_model_instance_method = app_model_instance_method.method;
    void* app_model_instance = nullptr;
    void* static_data_instance = nullptr;
    if (safe_runtime_invoke_object(app_model_instance_method.method, nullptr,
                                   &app_model_instance)) {
        safe_runtime_invoke_object(app_model_static_data_method.method,
                                   app_model_instance,
                                   &static_data_instance);
    }
    g_app_model_instance.store(app_model_instance, std::memory_order_release);
    g_app_model_read_user_method = app_model_read_user_method.method;
    g_user_read_guard_dispose_method =
        user_read_guard_dispose_method.method;
    g_static_data.store(static_data_instance, std::memory_order_release);
    diagnostic(std::string("static_data_ready app_model=") +
               std::to_string(reinterpret_cast<std::uintptr_t>(
                   app_model_instance)) +
               " static_data=" +
               std::to_string(reinterpret_cast<std::uintptr_t>(
                   static_data_instance)));
    void* localizer_instance =
        read_service_locator_localizer(api, service_locator);
    g_localizer.store(localizer_instance, std::memory_order_release);
    g_localize_method = localizer.klass
        ? api.class_get_method_from_name(localizer.klass, "Localize", 2)
        : nullptr;
    diagnostic(std::string("localizer_ready instance=") +
               std::to_string(reinterpret_cast<std::uintptr_t>(
                   localizer_instance)) +
               " method=" + (g_localize_method ? "1" : "0"));
    refresh_chimera_rotation_catalog(true);
    const MethodLocation create_manual_command =
        find_method_by_name(api, command_generator.klass, "CreateCmdManually");
    MethodLocation waiting_for_manual =
        find_method_by_name(api, command_generator.klass, "get_IsWaitingForManualCommand");
    if (!waiting_for_manual.method) {
        waiting_for_manual =
            find_method_by_name(api, command_generator.klass, "IsWaitingForManualCommand");
    }
    const MethodLocation on_enabled =
        find_method_by_name(api, battle_context.klass, "OnEnabled");
    const MethodLocation on_disabled =
        find_method_by_name(api, battle_context.klass, "OnDisabled");
    const MethodLocation request_command =
        find_method_by_name(api, command_generator.klass, "RequestCommand");
    const MethodLocation select_skill =
        find_method_by_name(api, command_generator.klass, "SelectSkill");
    const MethodLocation select_target =
        find_method_by_name(api, command_generator.klass, "SelectTargetManually");
    const MethodLocation mode_select_skill =
        find_method_by_name(api, client_battle_mode.klass, "SelectSkill");
    const MethodLocation push_active_skill_data = find_method_by_name(
        api, battle_hud_context.klass, "PushActiveSkillData");
    const MethodLocation battle_hud_pause_click = find_method_by_name(
        api, battle_hud_context.klass, "OnPauseClick");
    const MethodLocation get_acceptable_targets =
        find_method_by_name(api, client_battle_mode.klass,
                            "GetAcceptableTargets");
    const MethodLocation context_select_target =
        find_method_by_name(api, battle_context.klass, "OnTargetActorSelectedInView");
    const MethodLocation area_type =
        find_method_by_name(api, battle_context.klass, "get_AreaTypeId");
    const MethodLocation region_type =
        find_method_by_name(api, battle_context.klass, "get_RegionTypeId");
    const MethodLocation chimera_enabled =
        find_method_by_name(api, battle_context.klass, "ChimeraCompetitionEnabled");
    const MethodLocation enemy_boss_current = find_method_by_name(
        api, battle_context.klass, "EnemyTeamHasBossOnCurrentRound");
    const MethodLocation execute_cancel_chimera = find_method_by_name(
        api, battle_context.klass, "ExecuteCancelChimeraBattleCmd");
    const MethodLocation cancel_chimera = find_method_by_name(
        api, battle_context.klass, "CancelChimeraBattle");
    const MethodLocation heroes_selection_on_enabled = find_method_by_name(
        api, heroes_selection_chimera.klass, "OnEnabled");
    const MethodLocation heroes_selection_on_disabled = find_method_by_name(
        api, heroes_selection_chimera.klass, "OnDisabled");
    const MethodLocation heroes_selection_refresh = find_method_by_name(
        api, heroes_selection_chimera.klass, "Refresh");
    const MethodLocation heroes_selection_start_battle_click =
        find_method_by_name(api, heroes_selection_chimera.klass,
                            "StartBattleClick");
    const MethodLocation heroes_selection_filled = find_method_by_name(
        api, heroes_selection_chimera.klass, "FilledHeroesSelection");
    const MethodLocation heroes_selection_heroes = find_method_by_name(
        api, heroes_selection_chimera.klass, "HeroesForBattle");
    const MethodLocation heroes_selection_area = find_method_by_name(
        api, heroes_selection_chimera.klass, "get_AreaTypeId");
    const MethodLocation heroes_selection_stage = find_method_by_name(
        api, heroes_selection_chimera.klass, "get_StageId");
    const MethodLocation auto_battle_selected = find_method_by_name(
        api, auto_battle_checkbox.klass, "get_AutoBattleSelected");
    const MethodLocation quick_battle_active = find_method_by_name(
        api, chimera_quick_battle_checkbox.klass, "get_Active");
    const MethodLocation heroes_selection_start_battle = find_method_by_name(
        api, heroes_selection_chimera.klass, "StartBattle");
    const MethodLocation heroes_selection_hero_picked = find_method_by_name(
        api, heroes_selection_chimera.klass, "HeroPicked");
    const MethodLocation heroes_selection_hero = find_method_by_name(
        api, heroes_selection_chimera.klass, "Hero");
    const MethodLocation hydra_selection_on_enabled = find_method_by_name(
        api, heroes_selection_hydra.klass, "OnEnabled");
    const MethodLocation hydra_selection_on_disabled = find_method_by_name(
        api, heroes_selection_hydra.klass, "OnDisabled");
    const MethodLocation hydra_selection_refresh = find_method_by_name(
        api, heroes_selection_hydra.klass, "Refresh");
    const MethodLocation hydra_selection_start_battle_click =
        find_method_by_name(api, heroes_selection_hydra.klass,
                            "StartBattleClick");
    const MethodLocation hydra_selection_filled = find_method_by_name(
        api, heroes_selection_hydra.klass, "FilledHeroesSelection");
    const MethodLocation hydra_selection_heroes = find_method_by_name(
        api, heroes_selection_hydra.klass, "HeroesForBattle");
    const MethodLocation hydra_selection_area = find_method_by_name(
        api, heroes_selection_hydra.klass, "get_AreaTypeId");
    const MethodLocation hydra_selection_stage = find_method_by_name(
        api, heroes_selection_hydra.klass, "get_StageId");
    const MethodLocation hydra_selection_hero = find_method_by_name(
        api, heroes_selection_hydra.klass, "Hero");
    const MethodLocation hydra_quick_battle_active = find_method_by_name(
        api, hydra_quick_battle_checkbox.klass, "get_Active");
    const MethodLocation hydra_damage_counter_change = find_method_by_name(
        api, hydra_damage_counter.klass, "ChangeValueForHead");
    const MethodLocation battle_finish_on_enabled = find_method_by_name(
        api, battle_finish_alliance_chimera.klass, "OnEnabled");
    const MethodLocation battle_finish_save_result = find_method_by_name(
        api, battle_finish_alliance_chimera.klass, "OnSaveResultPressed");
    const MethodLocation battle_finish_quick_restart = find_method_by_name(
        api, battle_finish_alliance_chimera.klass, "QuickRestartBattle");
    const MethodLocation battle_finish_total_damage = find_method_by_name(
        api, battle_finish_alliance_chimera.klass, "TotalDamageDealt");
    const MethodLocation hydra_finish_total_damage = find_method_by_name(
        api, battle_finish_alliance_hydra.klass, "TotalDamageDealt");
    const MethodLocation hydra_finish_restart_pressed = find_method_by_name(
        api, battle_finish_alliance_hydra.klass, "OnRestartPressed");
    const MethodLocation battle_finish_completed_challenges =
        find_method_by_name(api, battle_finish_alliance_chimera.klass,
                            "CompletedChallenges");
    g_create_manual_method = create_manual_command.method;
    g_acceptable_targets_method = get_acceptable_targets.method;
    g_area_type_method = area_type.method;
    g_region_type_method = region_type.method;
    g_chimera_enabled_method = chimera_enabled.method;
    g_enemy_boss_current_method = enemy_boss_current.method;
    g_selection_filled_method = heroes_selection_filled.method;
    g_selection_heroes_method = heroes_selection_heroes.method;
    g_selection_hero_method = heroes_selection_hero.method;
    g_selection_area_method = heroes_selection_area.method;
    g_selection_stage_method = heroes_selection_stage.method;
    g_selection_start_battle_click_method =
        heroes_selection_start_battle_click.method;
    g_selection_hero_picked_method = heroes_selection_hero_picked.method;
    g_auto_battle_selected_method = auto_battle_selected.method;
    g_quick_battle_active_method = quick_battle_active.method;
    g_hydra_selection_filled_method = hydra_selection_filled.method;
    g_hydra_selection_heroes_method = hydra_selection_heroes.method;
    g_hydra_selection_hero_method = hydra_selection_hero.method;
    g_hydra_selection_area_method = hydra_selection_area.method;
    g_hydra_selection_stage_method = hydra_selection_stage.method;
    g_hydra_quick_battle_active_method = hydra_quick_battle_active.method;
    g_hydra_selection_start_battle_click_method =
        hydra_selection_start_battle_click.method;
    g_result_completed_challenges_method =
        battle_finish_completed_challenges.method;
    g_execute_cancel_chimera_method = execute_cancel_chimera.method;
    g_cancel_chimera_method = cancel_chimera.method;
    g_hydra_total_damage_method =
        find_hydra_total_damage_method(hydra_extensions.klass);
    g_hydra_result_restart_pressed_method =
        hydra_finish_restart_pressed.method;
    const bool hooks_installed =
        install_capture_hooks(on_enabled, on_disabled, request_command,
                              select_skill, select_target, mode_select_skill,
                              push_active_skill_data,
                              battle_hud_pause_click,
                              context_select_target, get_acceptable_targets,
                              create_manual_command,
                              heroes_selection_on_enabled,
                              heroes_selection_on_disabled,
                              heroes_selection_refresh,
                              heroes_selection_start_battle_click,
                              battle_finish_total_damage);
    const bool hydra_selection_hooks_installed = hooks_installed &&
        install_hydra_selection_hooks(
            hydra_selection_on_enabled, hydra_selection_on_disabled,
            hydra_selection_refresh, hydra_selection_start_battle_click,
            hydra_finish_total_damage, hydra_damage_counter_change);
    g_get_area_type = reinterpret_cast<InstanceIntGetter>(native_method_pointer(area_type));
    g_get_region_type =
        reinterpret_cast<InstanceIntGetter>(native_method_pointer(region_type));
    g_get_chimera_enabled = reinterpret_cast<InstanceBoolGetter>(
        native_method_pointer(chimera_enabled));
    g_get_enemy_boss_current = reinterpret_cast<InstanceBoolGetter>(
        native_method_pointer(enemy_boss_current));
    const bool window_dispatch_installed = install_window_dispatch();

    std::ostringstream output;
    output << R"({"type":"probe","ok":true,"observeOnly":true,)";
    append_bool(output, "battleProcessor", battle_processor.klass);
    append_bool(output, "clientBattleViewContext", battle_context.klass);
    append_bool(output, "clientCommandGenerator", command_generator.klass);
    append_bool(output, "chimeraChallengeContext", challenge_context.klass);
    append_bool(output, "chimeraForm", chimera_form.klass);
    append_bool(output, "createCmdManually", create_manual_command.method);
    append_bool(output, "waitingForManualCommand", waiting_for_manual.method);
    append_bool(output, "captureHooksInstalled", hooks_installed);
    append_bool(output, "hydraSelectionHooksInstalled",
                hydra_selection_hooks_installed);
    append_bool(output, "hydraDamageCounterHookInstalled",
                g_hydra_damage_counter_hook_installed.load(
                    std::memory_order_acquire));
    append_bool(output, "hydraDamageMetric",
                g_hydra_total_damage_method != nullptr);
    append_bool(output, "hydraResultRegroupButton",
                g_hydra_result_restart_pressed_method != nullptr);
    append_bool(output, "windowDispatcherInstalled", window_dispatch_installed);
    output << "\"resolvedClasses\":{";
    append_class_location(output, "BattleProcessor", battle_processor);
    append_class_location(output, "ClientBattleViewContext", battle_context);
    append_class_location(output, "ClientCommandGenerator", command_generator);
    append_class_location(output, "ClientBattleMode", client_battle_mode);
    append_class_location(output, "ClientLiveBattleMode", live_battle_mode);
    append_class_location(output, "ChimeraChallengeContext", challenge_context);
    append_class_location(output, "StaticAllianceData", static_alliance_data);
    append_class_location(output, "ChimeraChallengeType",
                          chimera_challenge_type);
    append_class_location(output, "ChimeraChallengeRewards",
                          chimera_challenge_rewards);
    append_class_location(output, "ChimeraChallengeTypeContainer",
                          chimera_challenge_type_container);
    append_class_location(output, "ChimeraChallengeNameCombiner",
                          chimera_challenge_name_combiner);
    append_class_location(output, "ChimeraChallengesOverlayContext",
                          chimera_challenges_overlay);
    append_class_location(output, "ChallengesByChimeraChallengeFormContext",
                          challenges_by_chimera_form);
    append_class_location(output, "ChallengesByChimeraChallengePartContext",
                          challenges_by_chimera_part);
    append_class_location(output, "ChimeraChallengeDialogContext",
                          chimera_challenge_dialog);
    append_class_location(output, "ChimeraChallengeFormContext",
                          chimera_challenge_form_context);
    append_class_location(output, "ChimeraChallengeExtensions",
                          chimera_challenge_extensions);
    append_class_location(output, "AllianceChimeraType",
                          alliance_chimera_type);
    append_class_location(output, "FlexibleReward", flexible_reward);
    append_class_location(output, "FlexibleRewardInfo",
                          flexible_reward_info);
    append_class_location(output, "FlexibleRewardType",
                          flexible_reward_type);
    append_class_location(output, "AllianceChimeraDifficulty",
                          alliance_chimera_difficulty);
    append_class_location(output, "ChimeraChallengePart",
                          chimera_challenge_part);
    append_class_location(output, "ChimeraChallengeDifficulty",
                          chimera_challenge_difficulty);
    append_class_location(output, "StatusEffectTypeId",
                          status_effect_type_id);
    append_class_location(output, "ResourceTypeId", resource_type_id);
    append_class_location(output, "BattleStatistics", battle_statistics);
    append_class_location(output, "ChimeraMultiplierStatistics",
                          chimera_multiplier_statistics);
    append_class_location(output, "LongFixed", long_fixed);
    append_class_location(output, "FinishBattleInfo", finish_battle_info);
    append_class_location(output, "Resources", resources);
    append_class_location(output, "UserPrize", user_prize);
    append_class_location(output, "HeroesSelectionChimeraDialogContext",
                          heroes_selection_chimera);
    append_class_location(output, "HeroesSelectionHydraDialogContext",
                          heroes_selection_hydra);
    append_class_location(output, "AutoBattleCheckboxContext",
                          auto_battle_checkbox);
    append_class_location(output, "ChimeraQuickBattleCheckboxContext",
                          chimera_quick_battle_checkbox);
    append_class_location(output, "HydraQuickBattleCheckboxContext",
                          hydra_quick_battle_checkbox);
    append_class_location(output, "HydraDamageCounter",
                          hydra_damage_counter);
    append_class_location(output, "BoolProperty", bool_property);
    append_class_location(output, "BattleFinishAllianceChimeraDialogContext",
                          battle_finish_alliance_chimera);
    append_class_location(output, "BattleFinishAllianceHydraDialogContext",
                          battle_finish_alliance_hydra);
    append_class_location(output, "BattleFinishAllianceBossDialogContext",
                          battle_finish_alliance_boss);
    append_class_location(output, "AllianceChimeraChallengeContext",
                          alliance_chimera_challenge);
    append_class_location(output, "ChimeraChallengeRewardContext",
                          chimera_challenge_reward);
    append_class_location(output, "ChimeraCompletedChallengesTooltipContext",
                          completed_challenges_tooltip);
    append_class_location(output, "BattleResult", battle_result);
    append_class_location(output, "ChimeraForm", chimera_form);
    append_class_location(output, "AllianceHydraType",
                          alliance_hydra_type);
    append_class_location(output, "AllianceHydraDifficulty",
                          alliance_hydra_difficulty);
    append_class_location(output, "HydraHead", hydra_head);
    append_class_location(output, "HydraNeck", hydra_neck);
    append_class_location(output, "DigestionInfo", digestion_info);
    append_class_location(output, "SkillData", skill_data);
    append_class_location(output, "SkillAvatarWithCooldownContext",
                          skill_avatar);
    append_class_location(output, "SkillContext", skill_context);
    append_class_location(output, "BattleHUDContext", battle_hud_context);
    append_class_location(output, "BattleHUDState", hud_state);
    append_class_location(output, "BattleActorUIContext", actor_ui);
    append_class_location(output, "BattleActorUIWithSkillsContext",
                          actor_with_skills_ui);
    append_class_location(output, "Localizer", localizer);
    append_class_location(output, "ILocalizerService", localizer_service);
    append_class_location(output, "ServiceLocator", service_locator);
    append_class_location(output, "LocalizedText", localized_text);
    append_class_location(output, "LocalizeUtils", localize_utils);
    append_class_location(output, "HeroType", hero_type);
    append_class_location(output, "SharedLTextKey", shared_ltext_key);
    append_class_location(output, "SkillType", skill_type);
    append_class_location(output, "LocalizationStorage", localization_storage,
                          true);
    append_class_location(output, "AppModel", app_model);
    append_class_location(output, "User", user_model);
    append_class_location(output, "UserWrapper", user_wrapper);
    append_class_location(output, "UserReadGuard", user_read_guard);
    append_class_location(output, "UserSession", user_session);
    append_class_location(output, "UserSocialData", user_social_data);
    append_class_location(output, "SocialWrapper", social_wrapper);
    append_class_location(output, "UserGameData", user_game_data);
    append_class_location(output, "AccountWrapper", account_wrapper);
    append_class_location(output, "UpdatableUserAccount",
                          updatable_user_account);
    append_class_location(output, "SharedUserAccount", shared_user_account);
    append_class_location(output, "UpdatableUserRaidShowData",
                          updatable_raid_show);
    append_class_location(output, "UpdatableUserGameSettings",
                          updatable_game_settings);
    append_class_location(output, "SharedUserGameSettings",
                          shared_game_settings);
    append_class_location(output, "ClientStaticData", client_static_data,
                          true);
    append_class_location(output, "StaticHeroData", static_hero_data);
    append_class_location(output, "StaticSkillData", static_skill_data,
                          true);
    append_class_location(output, "ChimeraComponent", chimera_component);
    append_class_location(output, "ChimeraDifficultyComponent",
                          chimera_difficulty_component);
    append_class_location(output, "ChimeraHpState", chimera_hp_state);
    append_class_location(output, "ChimeraTurn", chimera_turn);
    append_class_location(output, "ChimeraChallengeTurn",
                          chimera_challenge_turn);
    append_class_location(output, "BattleContextModel", battle_context_model);
    append_class_location(output, "BattleState", battle_state);
    append_class_location(output, "BattleStateSnapshot",
                          battle_state_snapshot);
    append_class_location(output, "HydraExtensions", hydra_extensions);
    append_class_location(output, "BattleHeroSnapshot",
                          battle_hero_snapshot);
    append_class_location(output, "TeamId", team_id);
    append_class_location(output, "BattleHero", battle_hero);
    append_class_location(output, "BattleTeam", battle_team);
    append_class_location(output, "Fixed", fixed_number);
    append_class_location(output, "AppliedEffect", applied_effect);
    append_class_location(output, "BattleSkill", battle_skill);
    append_class_location(output, "HeroState", hero_state);
    append_class_location(output, "HeroForm", hero_form);
    append_class_location(output, "Challenge", challenge);
    append_class_location(output, "EffectType", effect_type);
    append_class_location(output, "StaticEffectData", static_effect_data);
    append_class_location(output, "EffectKindId", effect_kind_id, false);
    output << "},\"resolvedMethods\":{";
    append_method_location(output, "CreateCmdManually", create_manual_command);
    append_method_location(output, "IsWaitingForManualCommand", waiting_for_manual);
    append_method_location(output, "SelectSkill", select_skill);
    append_method_location(output, "SelectTargetManually", select_target);
    append_method_location(output, "ClientBattleMode.SelectSkill", mode_select_skill);
    append_method_location(output, "PushActiveSkillData",
                           push_active_skill_data);
    append_method_location(output, "BattleHUDContext.OnPauseClick",
                           battle_hud_pause_click);
    append_method_location(output, "GetAcceptableTargets",
                           get_acceptable_targets);
    append_method_location(output, "OnTargetActorSelectedInView", context_select_target,
                           true);
    append_method_location(output, "get_AreaTypeId", area_type);
    append_method_location(output, "get_RegionTypeId", region_type);
    append_method_location(output, "ChimeraCompetitionEnabled", chimera_enabled);
    append_method_location(output, "EnemyTeamHasBossOnCurrentRound",
                           enemy_boss_current, false);
    output << ',';
    append_method_location(output, "ExecuteCancelChimeraBattleCmd",
                           execute_cancel_chimera);
    append_method_location(output, "CancelChimeraBattle", cancel_chimera);
    append_method_location(output, "HeroesSelectionChimera.OnEnabled",
                           heroes_selection_on_enabled);
    append_method_location(output, "HeroesSelectionChimera.Refresh",
                           heroes_selection_refresh);
    append_method_location(output, "HeroesSelectionChimera.Hero",
                           heroes_selection_hero);
    append_method_location(output, "HeroesSelectionChimera.StartBattle",
                           heroes_selection_start_battle);
    append_method_location(output, "BattleFinishAllianceChimera.OnEnabled",
                           battle_finish_on_enabled);
    append_method_location(output, "BattleFinishAllianceChimera.OnSaveResultPressed",
                           battle_finish_save_result);
    append_method_location(output, "BattleFinishAllianceChimera.QuickRestartBattle",
                           battle_finish_quick_restart, false);
    output << ',';
    append_method_location(output, "BattleFinishAllianceHydra.OnRestartPressed",
                           hydra_finish_restart_pressed, false);
    output << ',';
    append_method_location(output, "HydraDamageCounter.ChangeValueForHead",
                           hydra_damage_counter_change, false);
    output << "},\"memberInventory\":{\"ClientCommandGenerator\":";
    append_class_members(output, api, command_generator);
    output << ",\"ChimeraChallengeContext\":";
    append_class_members(output, api, challenge_context);
    output << ",\"StaticAllianceData\":";
    append_class_members(output, api, static_alliance_data);
    output << ",\"ChimeraChallengeType\":";
    append_class_members(output, api, chimera_challenge_type);
    output << ",\"ChimeraChallengeRewards\":";
    append_class_members(output, api, chimera_challenge_rewards);
    output << ",\"ChimeraChallengeTypeContainer\":";
    append_class_members(output, api, chimera_challenge_type_container);
    output << ",\"ChimeraChallengeNameCombiner\":";
    append_class_members(output, api, chimera_challenge_name_combiner);
    output << ",\"ChimeraChallengesOverlayContext\":";
    append_class_members(output, api, chimera_challenges_overlay);
    output << ",\"ChallengesByChimeraChallengeFormContext\":";
    append_class_members(output, api, challenges_by_chimera_form);
    output << ",\"ChallengesByChimeraChallengePartContext\":";
    append_class_members(output, api, challenges_by_chimera_part);
    output << ",\"ChimeraChallengeDialogContext\":";
    append_class_members(output, api, chimera_challenge_dialog);
    output << ",\"ChimeraChallengeFormContext\":";
    append_class_members(output, api, chimera_challenge_form_context);
    output << ",\"ChimeraChallengeExtensions\":";
    append_class_members(output, api, chimera_challenge_extensions);
    output << ",\"AllianceChimeraType\":";
    append_class_members(output, api, alliance_chimera_type);
    output << ",\"FlexibleReward\":";
    append_class_members(output, api, flexible_reward);
    output << ",\"FlexibleRewardInfo\":";
    append_class_members(output, api, flexible_reward_info);
    output << ",\"FlexibleRewardType\":";
    append_class_members(output, api, flexible_reward_type);
    output << ",\"AllianceChimeraDifficulty\":";
    append_class_members(output, api, alliance_chimera_difficulty);
    output << ",\"ChimeraChallengePart\":";
    append_class_members(output, api, chimera_challenge_part);
    output << ",\"ChimeraChallengeDifficulty\":";
    append_class_members(output, api, chimera_challenge_difficulty);
    output << ",\"StatusEffectTypeId\":";
    append_class_members(output, api, status_effect_type_id);
    output << ",\"ResourceTypeId\":";
    append_class_members(output, api, resource_type_id);
    output << ",\"BattleStatistics\":";
    append_class_members(output, api, battle_statistics);
    output << ",\"ChimeraMultiplierStatistics\":";
    append_class_members(output, api, chimera_multiplier_statistics);
    output << ",\"LongFixed\":";
    append_class_members(output, api, long_fixed);
    output << ",\"FinishBattleInfo\":";
    append_class_members(output, api, finish_battle_info);
    output << ",\"Resources\":";
    append_class_members(output, api, resources);
    output << ",\"UserPrize\":";
    append_class_members(output, api, user_prize);
    output << ",\"ClientBattleViewContext\":";
    append_class_members(output, api, battle_context);
    output << ",\"ClientLiveBattleMode\":";
    append_class_members(output, api, live_battle_mode);
    output << ",\"SkillData\":";
    append_class_members(output, api, skill_data);
    output << ",\"SkillAvatarWithCooldownContext\":";
    append_class_members(output, api, skill_avatar);
    output << ",\"SkillContext\":";
    append_class_members(output, api, skill_context);
    output << ",\"BattleHUDContext\":";
    append_class_members(output, api, battle_hud_context);
    output << ",\"BattleHUDState\":";
    append_class_members(output, api, hud_state);
    output << ",\"BattleActorUIContext\":";
    append_class_members(output, api, actor_ui);
    output << ",\"BattleActorUIWithSkillsContext\":";
    append_class_members(output, api, actor_with_skills_ui);
    output << ",\"Localizer\":";
    append_class_members(output, api, localizer);
    output << ",\"ILocalizerService\":";
    append_class_members(output, api, localizer_service);
    output << ",\"LocalizedText\":";
    append_class_members(output, api, localized_text);
    output << ",\"LocalizeUtils\":";
    append_class_members(output, api, localize_utils);
    output << ",\"HeroType\":";
    append_class_members(output, api, hero_type);
    output << ",\"SharedLTextKey\":";
    append_class_members(output, api, shared_ltext_key);
    output << ",\"SkillType\":";
    append_class_members(output, api, skill_type);
    output << ",\"LocalizationStorage\":";
    append_class_members(output, api, localization_storage);
    output << ",\"ServiceLocator\":";
    append_class_members(output, api, service_locator);
    output << ",\"AppModel\":";
    append_class_members(output, api, app_model);
    output << ",\"User\":";
    append_class_members(output, api, user_model);
    output << ",\"UserWrapper\":";
    append_class_members(output, api, user_wrapper);
    output << ",\"UserReadGuard\":";
    append_class_members(output, api, user_read_guard);
    output << ",\"UserSession\":";
    append_class_members(output, api, user_session);
    output << ",\"UserSocialData\":";
    append_class_members(output, api, user_social_data);
    output << ",\"SocialWrapper\":";
    append_class_members(output, api, social_wrapper);
    output << ",\"UserGameData\":";
    append_class_members(output, api, user_game_data);
    output << ",\"AccountWrapper\":";
    append_class_members(output, api, account_wrapper);
    output << ",\"UpdatableUserAccount\":";
    append_class_members(output, api, updatable_user_account);
    output << ",\"SharedUserAccount\":";
    append_class_members(output, api, shared_user_account);
    output << ",\"UpdatableUserRaidShowData\":";
    append_class_members(output, api, updatable_raid_show);
    output << ",\"UpdatableUserGameSettings\":";
    append_class_members(output, api, updatable_game_settings);
    output << ",\"SharedUserGameSettings\":";
    append_class_members(output, api, shared_game_settings);
    output << ",\"ClientStaticData\":";
    append_class_members(output, api, client_static_data);
    output << ",\"StaticHeroData\":";
    append_class_members(output, api, static_hero_data);
    output << ",\"StaticSkillData\":";
    append_class_members(output, api, static_skill_data);
    output << ",\"BattleProcessor\":";
    append_class_members(output, api, battle_processor);
    output << ",\"ChimeraComponent\":";
    append_class_members(output, api, chimera_component);
    output << ",\"ChimeraDifficultyComponent\":";
    append_class_members(output, api, chimera_difficulty_component);
    output << ",\"ChimeraHpState\":";
    append_class_members(output, api, chimera_hp_state);
    output << ",\"ChimeraTurn\":";
    append_class_members(output, api, chimera_turn);
    output << ",\"ChimeraChallengeTurn\":";
    append_class_members(output, api, chimera_challenge_turn);
    output << ",\"BattleContextModel\":";
    append_class_members(output, api, battle_context_model);
    output << ",\"BattleState\":";
    append_class_members(output, api, battle_state);
    output << ",\"BattleStateSnapshot\":";
    append_class_members(output, api, battle_state_snapshot);
    output << ",\"HydraExtensions\":";
    append_class_members(output, api, hydra_extensions);
    output << ",\"BattleHeroSnapshot\":";
    append_class_members(output, api, battle_hero_snapshot);
    output << ",\"TeamId\":";
    append_class_members(output, api, team_id);
    output << ",\"BattleHero\":";
    append_class_members(output, api, battle_hero);
    output << ",\"BattleTeam\":";
    append_class_members(output, api, battle_team);
    output << ",\"Fixed\":";
    append_class_members(output, api, fixed_number);
    output << ",\"AppliedEffect\":";
    append_class_members(output, api, applied_effect);
    output << ",\"BattleSkill\":";
    append_class_members(output, api, battle_skill);
    output << ",\"HeroState\":";
    append_class_members(output, api, hero_state);
    output << ",\"HeroForm\":";
    append_class_members(output, api, hero_form);
    output << ",\"Challenge\":";
    append_class_members(output, api, challenge);
    output << ",\"ChimeraForm\":";
    append_class_members(output, api, chimera_form);
    output << ",\"EffectType\":";
    append_class_members(output, api, effect_type);
    output << ",\"StaticEffectData\":";
    append_class_members(output, api, static_effect_data);
    output << ",\"EffectKindId\":";
    append_class_members(output, api, effect_kind_id);
    output << ",\"HeroesSelectionChimeraDialogContext\":";
    append_class_members(output, api, heroes_selection_chimera);
    output << ",\"AutoBattleCheckboxContext\":";
    append_class_members(output, api, auto_battle_checkbox);
    output << ",\"ChimeraQuickBattleCheckboxContext\":";
    append_class_members(output, api, chimera_quick_battle_checkbox);
    output << ",\"BoolProperty\":";
    append_class_members(output, api, bool_property);
    output << ",\"BattleFinishAllianceChimeraDialogContext\":";
    append_class_members(output, api, battle_finish_alliance_chimera);
    output << ",\"BattleFinishAllianceHydraDialogContext\":";
    append_class_members(output, api, battle_finish_alliance_hydra);
    output << ",\"BattleFinishAllianceBossDialogContext\":";
    append_class_members(output, api, battle_finish_alliance_boss);
    output << ",\"AllianceChimeraChallengeContext\":";
    append_class_members(output, api, alliance_chimera_challenge);
    output << ",\"ChimeraChallengeRewardContext\":";
    append_class_members(output, api, chimera_challenge_reward);
    output << ",\"ChimeraCompletedChallengesTooltipContext\":";
    append_class_members(output, api, completed_challenges_tooltip);
    output << ",\"BattleResult\":";
    append_class_members(output, api, battle_result);
    output << '}';
    output << ",\"localizerFieldReferences\":";
    append_localizer_field_references(output, api);
    output << ",\"chimeraFormReferences\":";
    append_type_references(output, api, "ChimeraForm");
    output << ",\"skillDataReferences\":";
    append_type_references(output, api, "SkillData");
    output << ",\"skillContextReferences\":";
    append_type_references(output, api, "SkillContext");
    output << ",\"accountNameMemberReferences\":";
    append_account_name_member_references(output, api);
    output << ",\"localizationTest\":";
    append_localization_test(output, api, localizer, service_locator,
                             "Skill 47102 name");
    const ResolvedText probe_hero_name = resolve_static_hero_name(4716);
    const ResolvedText probe_skill_name = resolve_static_skill_name(47102);
    const ResolvedText probe_boss_name = resolve_static_hero_name(26866);
    output << ",\"staticChimeraCatalog\":";
    append_static_chimera_catalog(
        output, alliance_chimera_difficulty.klass, chimera_form.klass,
        chimera_challenge_part.klass, chimera_challenge_difficulty.klass,
        status_effect_type_id.klass, flexible_reward_type.klass,
        resource_type_id.klass);
    output << ",\"heroCatalog\":";
    append_all_static_hero_catalog(output);
    output << ",\"staticNameTest\":{\"hero4716\":{\"key\":\""
           << json_escape(probe_hero_name.key) << "\",\"name\":\""
           << json_escape(probe_hero_name.display)
           << "\"},\"skill47102\":{\"key\":\""
           << json_escape(probe_skill_name.key) << "\",\"name\":\""
           << json_escape(probe_skill_name.display)
           << "\"},\"boss26866\":{\"key\":\""
           << json_escape(probe_boss_name.key) << "\",\"name\":\""
           << json_escape(probe_boss_name.display) << "\"}}";
    output << ",\"accountModelSnapshot\":";
    append_account_model_snapshot(output, app_model_instance);
    output << ",\"accountNameCandidates\":";
    append_account_name_candidates(output, api, app_model_instance);
    output << '}';
    diagnostic("type_probe_complete");
    log_account_identity(app_model_instance);
    set_agent_state(kAgentStateReady,
                    hooks_installed && window_dispatch_installed);
    publish(output.str());

    if (attached_thread) {
        api.thread_detach(attached_thread);
    }
    return 0;
}

}  // namespace

extern "C" __declspec(dllexport) DWORD WINAPI
RaidChimeraAgentQueueCommand(LPVOID parameter) {
    if (!is_raid_process() || !parameter ||
        !g_shared_state ||
        g_shared_state->ready_state != kAgentStateReady ||
        !g_shared_state->hooks_ready ||
        !g_window_dispatch_installed.load(std::memory_order_acquire) ||
        !g_game_window || !g_command_message) {
        return 3;
    }

    QueueCommandRequest request{};
    if (!safe_read(parameter, 0, request) || request.magic != kCommandMagic ||
        request.version != kCommandVersion || !request.session_id ||
        !request.generator || !request.mode ||
        !request.skill_data || request.target_id < 0 || request.skill_id < 0 ||
        request.skill_id > 20 || request.verified_skill_type_id <= 0 ||
        request.expected_round == INT_MIN ||
        request.expected_turn == INT_MIN ||
        request.expected_player_turn_count == INT_MIN ||
        request.expected_active_hero_id < 0 ||
        request.expected_active_hero_turn_count == INT_MIN ||
        request.expected_active_hero_form_index == INT_MIN) {
        return 2;
    }
    if (g_takeover_state.load(std::memory_order_acquire) != 1 ||
        g_takeover_session.load(std::memory_order_acquire) !=
            request.session_id) {
        return 6;
    }

    AcquireSRWLockExclusive(&g_command_lock);
    if (g_command_pending.load(std::memory_order_acquire)) {
        ReleaseSRWLockExclusive(&g_command_lock);
        return 4;
    }
    g_pending_command = request;
    g_command_pending.store(true, std::memory_order_release);
    ReleaseSRWLockExclusive(&g_command_lock);

    if (!PostMessageW(g_game_window, g_command_message, 0, 0)) {
        g_command_pending.store(false, std::memory_order_release);
        diagnostic("command_post_failed error=" +
                   std::to_string(GetLastError()));
        return 5;
    }
    diagnostic("command_queued nonce=" + std::to_string(request.nonce) +
               " execute=" +
               std::to_string((request.flags & kCommandFlagExecute) ? 1 : 0));
    return 1;
}

extern "C" __declspec(dllexport) DWORD WINAPI
RaidChimeraAgentQueueLifecycleCommand(LPVOID parameter) {
    if (!is_raid_process() || !parameter || !g_shared_state ||
        g_shared_state->ready_state != kAgentStateReady ||
        !g_shared_state->hooks_ready ||
        !g_window_dispatch_installed.load(std::memory_order_acquire) ||
        !g_game_window || !g_command_message) {
        return 3;
    }
    LifecycleCommandRequest request{};
    if (!safe_read(parameter, 0, request) ||
        request.magic != kLifecycleCommandMagic ||
        request.version != kLifecycleCommandVersion || !request.session_id ||
        (request.action != kLifecycleStartBattle &&
         request.action != kLifecycleFreeRegroup &&
         request.action != kLifecyclePrepareFreeRegroup &&
         request.action != kLifecycleRefreshTeamSelection &&
         request.action != kLifecycleSelectHeroes &&
         request.action != kLifecycleRestartHydraResult) ||
         !request.context ||
         !request.nonce) {
        return 2;
    }
    if (request.action == kLifecycleSelectHeroes &&
        request.hero_count != request.hero_ids.size()) {
        return 2;
    }
    if (g_takeover_state.load(std::memory_order_acquire) != 1 ||
        g_takeover_session.load(std::memory_order_acquire) !=
            request.session_id) {
        return 6;
    }
    AcquireSRWLockExclusive(&g_lifecycle_command_lock);
    if (g_lifecycle_command_pending.load(std::memory_order_acquire)) {
        ReleaseSRWLockExclusive(&g_lifecycle_command_lock);
        return 4;
    }
    g_pending_lifecycle_command = request;
    g_lifecycle_command_pending.store(true, std::memory_order_release);
    ReleaseSRWLockExclusive(&g_lifecycle_command_lock);
    if (!PostMessageW(g_game_window, g_command_message, 0, 0)) {
        g_lifecycle_command_pending.store(false, std::memory_order_release);
        diagnostic("lifecycle_command_post_failed error=" +
                   std::to_string(GetLastError()));
        return 5;
    }
    diagnostic("lifecycle_command_queued action=" +
               std::to_string(request.action) +
               " nonce=" + std::to_string(request.nonce));
    return 1;
}

extern "C" __declspec(dllexport) DWORD WINAPI
RaidChimeraAgentSetTakeover(LPVOID parameter) {
    if (!is_raid_process() || !parameter || !g_shared_state ||
        g_shared_state->ready_state != kAgentStateReady) {
        return 3;
    }
    TakeoverControlRequest request{};
    if (!safe_read(parameter, 0, request) ||
        request.magic != kControlMagic ||
        request.version != kControlVersion || !request.session_id) {
        return 2;
    }
    if (request.action == kControlBeginTakeover) {
        const std::uint64_t expected_user_id =
            static_cast<std::uint64_t>(request.nonce) |
            (static_cast<std::uint64_t>(request.reserved) << 32U);
        if ((request.flags & kControlFlagExpectedUser) == 0 ||
            !expected_user_id) {
            return 2;
        }
        LONG boss_mode = kTakeoverBossModeUnknown;
        if (request.flags & kControlFlagBossModeExplicit) {
            boss_mode = (request.flags & kControlFlagBossModeHydra)
                ? kTakeoverBossModeHydra
                : kTakeoverBossModeChimera;
        }
        const LONG state = g_takeover_state.load(std::memory_order_acquire);
        const std::uint64_t active_session =
            g_takeover_session.load(std::memory_order_acquire);
        if (state == 1 && active_session != request.session_id) {
            return 4;
        }
        begin_takeover(request.session_id, expected_user_id, boss_mode);
        return 1;
    }
    if (request.action == kControlEndTakeover) {
        end_takeover(request.session_id, "controller_disarmed");
        return 1;
    }
    return 2;
}

extern "C" __declspec(dllexport) DWORD WINAPI
RaidChimeraAgentSeedBattleContext(LPVOID parameter) {
    if (!is_raid_process() || !parameter || !g_shared_state ||
        g_shared_state->ready_state != kAgentStateReady ||
        !g_shared_state->hooks_ready) {
        return 3;
    }
    void* mode = nullptr;
    void* processor = nullptr;
    void* generator = nullptr;
    if (!object_has_class_name(parameter, "ClientBattleViewContext") ||
        !safe_read(parameter, 176, mode) || !mode ||
        !object_derives_from(mode, "ClientBattleMode") ||
        !safe_read(mode, 16, processor) || !processor ||
        !safe_read(mode, 104, generator) || !generator) {
        return 2;
    }
    capture_battle_context(parameter);
    g_selection_context.store(nullptr, std::memory_order_release);
    g_result_context.store(nullptr, std::memory_order_release);
    g_screen_state.store(kScreenUnknown, std::memory_order_release);
    if (g_shared_state) {
        publish_shared_json(g_shared_state->decision, "");
        publish_shared_json(g_shared_state->battle_ledger, "");
    }
    publish_lifecycle("battle_context_seeded_unverified");
    if (!g_game_window ||
        !SetTimer(g_game_window, kDecisionCaptureTimerId, 75, nullptr)) {
        diagnostic("battle_context_seed_decision_timer_failed");
        return 4;
    }
    return 1;
}

extern "C" __declspec(dllexport) DWORD WINAPI
RaidChimeraAgentSeedSelectionContext(LPVOID parameter) {
    if (!is_raid_process() || !parameter || !g_shared_state ||
        g_shared_state->ready_state != kAgentStateReady ||
        !g_shared_state->hooks_ready) {
        return 3;
    }
    if (!object_has_class_name(parameter,
                               "HeroesSelectionChimeraDialogContext") &&
        !object_has_class_name(parameter,
                               "HeroesSelectionHydraDialogContext")) {
        return 2;
    }
    g_battle_context.store(nullptr, std::memory_order_release);
    g_battle_mode.store(nullptr, std::memory_order_release);
    g_result_context.store(nullptr, std::memory_order_release);
    g_selection_user.store(nullptr, std::memory_order_release);
    void* attached_thread = nullptr;
    const bool attached_here = !g_api.thread_current();
    if (attached_here) {
        attached_thread = g_api.thread_attach(g_api.domain_get());
        if (!attached_thread) {
            return 4;
        }
    }
    capture_selection_state(parameter, "team_selection_context_recovered");
    if (g_game_window) {
        SetTimer(g_game_window, kSelectionCaptureTimerId,
                 kSelectionCaptureIntervalMs, nullptr);
    }
    SelectionSnapshot selection{};
    AcquireSRWLockShared(&g_ui_state_lock);
    selection = g_selection_snapshot;
    ReleaseSRWLockShared(&g_ui_state_lock);
    if (attached_here) {
        g_api.thread_detach(attached_thread);
    }
    return selection.valid ? 1 : 5;
}

extern "C" __declspec(dllexport) DWORD WINAPI RaidChimeraAgentShutdown(LPVOID) {
    set_agent_state(kAgentStateShuttingDown);
    end_takeover(0, "agent_shutting_down");
    if (!remove_window_dispatch()) {
        return 0;
    }
    if (g_hooks_installed.exchange(false, std::memory_order_acq_rel)) {
        const MH_STATUS disable_status = MH_DisableHook(MH_ALL_HOOKS);
        const MH_STATUS uninitialize_status = MH_Uninitialize();
        diagnostic("capture_hooks_removed disable=" + std::to_string(disable_status) +
                   " uninitialize=" + std::to_string(uninitialize_status));
    }
    g_battle_context.store(nullptr, std::memory_order_release);
    g_command_generator.store(nullptr, std::memory_order_release);
    g_battle_processor.store(nullptr, std::memory_order_release);
    g_selection_context.store(nullptr, std::memory_order_release);
    g_selection_user.store(nullptr, std::memory_order_release);
    g_app_model_instance.store(nullptr, std::memory_order_release);
    g_app_model_instance_method = nullptr;
    g_app_model_read_user_method = nullptr;
    g_user_read_guard_dispose_method = nullptr;
    g_result_context.store(nullptr, std::memory_order_release);
    g_lifecycle_command_pending.store(false, std::memory_order_release);
    g_screen_state.store(kScreenUnknown, std::memory_order_release);
    g_create_manual_method = nullptr;
    g_acceptable_targets_method = nullptr;
    g_area_type_method = nullptr;
    g_region_type_method = nullptr;
    g_chimera_enabled_method = nullptr;
    g_enemy_boss_current_method = nullptr;
    g_execute_cancel_chimera_method = nullptr;
    g_cancel_chimera_method = nullptr;
    g_selection_hero_method = nullptr;
    g_get_area_type = nullptr;
    g_get_region_type = nullptr;
    g_get_chimera_enabled = nullptr;
    g_get_enemy_boss_current = nullptr;
    g_localizer.store(nullptr, std::memory_order_release);
    g_localize_method = nullptr;
    g_static_data.store(nullptr, std::memory_order_release);
    AcquireSRWLockExclusive(&g_chimera_catalog_lock);
    for (std::string& catalog : g_chimera_catalog_by_difficulty) {
        catalog.clear();
    }
    g_chimera_rotation_identity_json.clear();
    g_chimera_rotation_fingerprint.clear();
    ReleaseSRWLockExclusive(&g_chimera_catalog_lock);
    AcquireSRWLockExclusive(&g_ui_state_lock);
    g_selection_snapshot = {};
    g_last_started_selection = {};
    ReleaseSRWLockExclusive(&g_ui_state_lock);
    clear_executed_turn();
    if (g_shared_state) {
        UnmapViewOfFile(g_shared_state);
        g_shared_state = nullptr;
    }
    if (g_shared_state_mapping) {
        CloseHandle(g_shared_state_mapping);
        g_shared_state_mapping = nullptr;
    }
    return 1;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        if (!is_raid_process()) {
            return TRUE;
        }
        diagnostic("dll_process_attach");
        HANDLE thread = CreateThread(nullptr, 0, probe_thread, nullptr, 0, nullptr);
        if (thread) {
            CloseHandle(thread);
        }
    }
    return TRUE;
}
