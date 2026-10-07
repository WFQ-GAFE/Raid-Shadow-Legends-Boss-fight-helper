// Exercises the production capture helpers with a fake managed runtime.
// No RAID process, DLL, or runtime is opened or loaded by this executable.
#include "agent.cpp"

#include <cassert>
#include <stdexcept>

namespace {

struct FakeObject {
    Il2CppClass* klass;
    void* section;
};

Il2CppClass* const kValidClass = reinterpret_cast<Il2CppClass*>(0x1000);
Il2CppClass* const kInvalidClass = reinterpret_cast<Il2CppClass*>(0x3031310d171e3032ULL);
const MethodInfo* const kSingletonGetter = reinterpret_cast<MethodInfo*>(0x2000);
const MethodInfo* const kStaticGetter = reinterpret_cast<MethodInfo*>(0x3000);
const MethodInfo* const kConstructor = reinterpret_cast<MethodInfo*>(0x4000);
const MethodInfo* const kReadUser = reinterpret_cast<MethodInfo*>(0x7000);
const MethodInfo* const kDispose = reinterpret_cast<MethodInfo*>(0x8000);
FieldInfo* const kSectionField = reinterpret_cast<FieldInfo*>(0x5000);
FieldInfo* const kLocalizerField = reinterpret_cast<FieldInfo*>(0x6000);

FakeObject old_model{kValidClass, nullptr};
FakeObject current_model{kValidClass, nullptr};
FakeObject old_static{kInvalidClass, nullptr};
FakeObject current_static{kValidClass, nullptr};
FakeObject skill_data{kValidClass, nullptr};
FakeObject constructed{kValidClass, nullptr};
FakeObject first_localizer{kValidClass, nullptr};
FakeObject second_localizer{kValidClass, nullptr};
FakeObject user_guard{kValidClass, nullptr};
void* singleton = &current_model;
void* localizer = &second_localizer;
bool fail_static_getter{};
bool mismatch_root{};
std::uintptr_t next_handle = 0x100000000ULL;
std::map<std::uintptr_t, void*> handles;
std::size_t creates{};
std::size_t releases{};
std::size_t disposals{};

bool rooted(void* object) {
    for (const auto& entry : handles) if (entry.second == object) return true;
    return false;
}

std::uintptr_t fake_root_new(void* object, bool) {
    const auto handle = ++next_handle;
    handles.emplace(handle, object);
    ++creates;
    return handle;
}

void* fake_root_target(std::uintptr_t handle) {
    if (mismatch_root) return nullptr;
    const auto found = handles.find(handle);
    return found == handles.end() ? nullptr : found->second;
}

void fake_root_free(std::uintptr_t handle) {
    assert(handles.erase(handle) == 1);
    ++releases;
}

void* fake_runtime_invoke(const MethodInfo* method, void* instance, void**, void** exception) {
    *exception = nullptr;
    if (method == kSingletonGetter) return singleton;
    if (method == kStaticGetter) {
        assert(instance == singleton);
        assert(rooted(instance));
        if (fail_static_getter) {
            *exception = reinterpret_cast<void*>(0x1);
            return nullptr;
        }
        return &current_static;
    }
    if (method == kConstructor) {
        // Allocation can collect other native temporaries inside a constructor.
        assert(instance == &constructed && rooted(instance));
        return nullptr;
    }
    if (method == kReadUser) {
        assert(instance == singleton);
        return &user_guard;
    }
    if (method == kDispose) {
        assert(instance == &user_guard);
        ++disposals;
        return nullptr;
    }
    assert(false && "Unexpected managed method");
    return nullptr;
}

FieldInfo* fake_class_fields(Il2CppClass* klass, void** iterator) {
    if (klass != kValidClass) {
        RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
        return nullptr;
    }
    // Current StaticData must stay rooted while its metadata is inspected.
    assert(rooted(&current_static));
    if (*iterator) return nullptr;
    *iterator = reinterpret_cast<void*>(0x1);
    return kSectionField;
}

const char* fake_field_name(FieldInfo* field) {
    assert(field == kSectionField);
    return "SkillData";
}

std::size_t fake_field_offset(FieldInfo* field) {
    assert(field == kSectionField);
    return offsetof(FakeObject, section);
}

Il2CppClass* fake_class_parent(Il2CppClass*) { return nullptr; }

const MethodInfo* fake_class_method(Il2CppClass* klass, const char*, int) {
    if (klass != kValidClass) RaiseException(EXCEPTION_ACCESS_VIOLATION, 0, 0, nullptr);
    return nullptr;
}

void fake_static_value(FieldInfo* field, void* destination) {
    assert(field == kLocalizerField);
    *static_cast<void**>(destination) = localizer;
}

void* fake_object_new(Il2CppClass* klass) {
    assert(klass == kValidClass);
    return &constructed;
}

void setup_fake_runtime() {
    g_api = {};
    g_api.runtime_invoke = fake_runtime_invoke;
    g_api.gchandle_new = fake_root_new;
    g_api.gchandle_get_target = fake_root_target;
    g_api.gchandle_free = fake_root_free;
    g_api.class_get_fields = fake_class_fields;
    g_api.field_get_name = fake_field_name;
    g_api.field_get_offset = fake_field_offset;
    g_api.class_get_parent = fake_class_parent;
    g_api.class_get_method_from_name = fake_class_method;
    g_api.field_static_get_value = fake_static_value;
    g_api.object_new = fake_object_new;
    g_app_model_instance_method = kSingletonGetter;
    g_app_model_static_data_method = kStaticGetter;
    g_app_model_read_user_method = kReadUser;
    g_user_read_guard_dispose_method = kDispose;
    current_static.section = &skill_data;
    g_localizer_static_field = kLocalizerField;
}

void test_current_owner_replaces_disposed_native_snapshot() {
    g_app_model_instance.store(&old_model);
    g_static_data.store(&old_static);
    {
        raid::capture::ScopedManagedRoots roots(capture_root_api());
        assert(static_data_section("SkillData") == &skill_data);
        assert(g_app_model_instance.load() == &current_model);
        assert(g_static_data.load() == &current_static);
        assert(rooted(&current_model) && rooted(&current_static));
        assert(!rooted(&old_model) && !rooted(&old_static));
    }
    assert(handles.empty());
}

void test_missing_singleton_and_static_getter_never_reuse_old_objects() {
    {
        raid::capture::ScopedManagedRoots roots(capture_root_api());
        singleton = nullptr;
        g_app_model_instance.store(&old_model);
        g_static_data.store(&old_static);
        assert(static_data_section("SkillData") == nullptr);
        assert(g_app_model_instance.load() == nullptr && g_static_data.load() == nullptr);
        singleton = &current_model;
        fail_static_getter = true;
        g_static_data.store(&old_static);
        assert(static_data_section("SkillData") == nullptr);
        assert(g_static_data.load() == nullptr);
        fail_static_getter = false;
    }
    assert(handles.empty());
}

void test_localizer_uses_current_static_field_and_handles_null() {
    g_localizer.store(&first_localizer);
    {
        raid::capture::ScopedManagedRoots roots(capture_root_api());
        assert(refresh_localizer_instance() == &second_localizer);
        assert(rooted(&second_localizer));
        localizer = nullptr;
        assert(refresh_localizer_instance() == nullptr);
        assert(g_localizer.load() == nullptr);
        localizer = &second_localizer;
    }
    assert(handles.empty());
}

void test_invalid_class_api_access_is_caught() {
    std::size_t offset = 123;
    assert(!find_field_offset(kInvalidClass, "SkillData", nullptr, offset));
    assert(offset == 123);
    assert(safe_class_method(kInvalidClass, "GetSkillType", 2) == nullptr);
}

void test_temporary_is_rooted_before_ctor_and_released_on_unwind() {
    try {
        raid::capture::ScopedManagedRoots roots(capture_root_api());
        void* object = nullptr;
        assert(raw_construct(kValidClass, kConstructor, nullptr, &object));
        assert(object == &constructed && rooted(object));
        throw std::runtime_error("Capture interrupted");
    } catch (const std::runtime_error&) {
    }
    assert(handles.empty());
}

void test_root_width_nested_lifetimes_and_failure_cleanup() {
    {
        raid::capture::ScopedManagedRoots outer(capture_root_api());
        assert(outer.keep(&current_model));
        assert(handles.begin()->first > UINT32_MAX);
        {
            raid::capture::ScopedManagedRoots inner(capture_root_api());
            assert(retain_capture_object(&skill_data));
            assert(rooted(&current_model) && rooted(&skill_data));
        }
        assert(rooted(&current_model) && !rooted(&skill_data));
        mismatch_root = true;
        assert(!outer.keep(&constructed));
        mismatch_root = false;
        assert(!rooted(&constructed));
    }
    assert(handles.empty() && creates == releases);
    raid::capture::ScopedManagedRoots missing({});
    assert(!missing.ready() && !missing.keep(&current_model));
}

void test_read_guard_disposes_after_root_failure_and_capture_exception() {
    const auto before = disposals;
    {
        raid::capture::ScopedManagedRoots roots(capture_root_api());
        assert(roots.keep(&current_model));
        void* user = nullptr;
        ScopedUserReadGuard guard{user};
        mismatch_root = true;
        assert(!safe_runtime_invoke_nullable_int_none(kReadUser, &current_model, &user));
        mismatch_root = false;
        assert(user == nullptr && disposals == before + 1);
    }
    assert(handles.empty() && disposals == before + 1);
    try {
        raid::capture::ScopedManagedRoots roots(capture_root_api());
        void* user = nullptr;
        ScopedUserReadGuard guard{user};
        assert(safe_runtime_invoke_nullable_int_none(kReadUser, &current_model, &user));
        assert(user == &user_guard && rooted(user));
        throw std::runtime_error("Read aborted");
    } catch (const std::runtime_error&) {
    }
    assert(handles.empty() && disposals == before + 2 && creates == releases);
}

std::size_t bonus_post_calls{};
bool bonus_post_fails{};
std::uint64_t expected_bonus_nonce{};

BOOL WINAPI fake_bonus_post_message(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    ++bonus_post_calls;
    assert(window == g_game_window && message == g_command_message && !wparam && !lparam);
    assert(g_bonus_pending.load() && !g_bonus_processing.load());
    assert(g_pending_bonus.nonce == expected_bonus_nonce);
    // A queued command message must be unable to drain A while A's own post
    // may still fail. This fails if posting moves outside the queue lock.
    const bool dispatcher_can_enter = TryAcquireSRWLockExclusive(&g_bonus_lock) != FALSE;
    if (dispatcher_can_enter) ReleaseSRWLockExclusive(&g_bonus_lock);
    assert(!dispatcher_can_enter);
    if (bonus_post_fails) {
        SetLastError(ERROR_NOT_ENOUGH_QUOTA);
        return FALSE;
    }
    return TRUE;
}

void assert_bonus_queue_unlocked() {
    assert(TryAcquireSRWLockExclusive(&g_bonus_lock));
    ReleaseSRWLockExclusive(&g_bonus_lock);
}

void test_failed_bonus_post_retracts_only_its_request() {
    const HWND previous_window = g_game_window;
    const UINT previous_message = g_command_message;
    g_game_window = reinterpret_cast<HWND>(0x9000);
    g_command_message = WM_APP + 3;
    assert(!g_shared_state && !g_bonus_pending.load() && !g_bonus_processing.load());
    BonusRequest request{};
    request.kind = kRequestTeamData;
    request.count = 1;
    request.hero_ids[0] = 42;
    request.nonce = expected_bonus_nonce = 901;
    bonus_post_fails = true;
    assert(queue_bonus_request(request, fake_bonus_post_message) == 5);
    assert(!g_bonus_pending.load() && !g_bonus_processing.load());
    assert_bonus_queue_unlocked();

    // B must queue after A's failure, keep its own nonce, and survive a busy
    // rejection of C. No game window or managed runtime is called by the mock.
    request.nonce = expected_bonus_nonce = 902;
    bonus_post_fails = false;
    assert(queue_bonus_request(request, fake_bonus_post_message) == 1);
    assert(g_bonus_pending.load() && g_pending_bonus.nonce == 902);
    assert(g_pending_bonus.count == 1 && g_pending_bonus.hero_ids[0] == 42);
    request.nonce = 903;
    assert(queue_bonus_request(request, fake_bonus_post_message) == 4);
    assert(g_bonus_pending.load() && g_pending_bonus.nonce == 902 && bonus_post_calls == 2);
    assert_bonus_queue_unlocked();
    drain_pending_bonus_request();  // Capture returns before managed calls without shared state.
    assert(!g_bonus_pending.load() && !g_bonus_processing.load());

    g_bonus_processing.store(true);
    assert(queue_bonus_request(request, fake_bonus_post_message) == 4);
    assert(!g_bonus_pending.load() && g_bonus_processing.load());
    assert(g_pending_bonus.nonce == 902 && bonus_post_calls == 2);
    g_bonus_processing.store(false);
    assert_bonus_queue_unlocked();
    g_game_window = previous_window;
    g_command_message = previous_message;
}

}  // namespace

int main() {
    setup_fake_runtime();
    test_current_owner_replaces_disposed_native_snapshot();
    test_missing_singleton_and_static_getter_never_reuse_old_objects();
    test_localizer_uses_current_static_field_and_handles_null();
    test_invalid_class_api_access_is_caught();
    test_temporary_is_rooted_before_ctor_and_released_on_unwind();
    test_root_width_nested_lifetimes_and_failure_cleanup();
    test_read_guard_disposes_after_root_failure_and_capture_exception();
    test_failed_bonus_post_retracts_only_its_request();
    return 0;
}
