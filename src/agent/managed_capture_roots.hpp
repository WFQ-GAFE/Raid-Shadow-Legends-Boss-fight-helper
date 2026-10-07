#pragma once

#include <cstdint>
#include <vector>

// A native capture retains its managed inputs and temporary results until all
// serialization has finished. Handles use the runtime's full pointer width.
namespace raid::capture {

struct RootApi {
    std::uintptr_t (*create)(void*){};
    void* (*target)(std::uintptr_t){};
    void (*release)(std::uintptr_t){};
};

class ScopedManagedRoots {
public:
    explicit ScopedManagedRoots(RootApi api) noexcept
        : api_(api), previous_(current_) {
        current_ = this;
    }

    ~ScopedManagedRoots() noexcept {
        current_ = previous_;
        if (api_.release) {
            for (auto iterator = roots_.rbegin(); iterator != roots_.rend(); ++iterator)
                api_.release(iterator->handle);
        }
    }

    ScopedManagedRoots(const ScopedManagedRoots&) = delete;
    ScopedManagedRoots& operator=(const ScopedManagedRoots&) = delete;

    bool ready() const noexcept { return api_.create && api_.target && api_.release; }

    bool keep(void* object) noexcept {
        if (!object) return true;
        if (!ready()) return false;
        for (const auto& root : roots_) {
            if (root.object == object) return true;
        }
        const auto handle = api_.create(object);
        if (!handle) return false;
        if (api_.target(handle) != object) {
            api_.release(handle);
            return false;
        }
        try {
            roots_.push_back({object, handle});
        } catch (...) {
            api_.release(handle);
            return false;
        }
        return true;
    }

    static bool keep_current(void* object) noexcept {
        return !current_ || current_->keep(object);
    }

private:
    struct Root {
        void* object;
        std::uintptr_t handle;
    };
    RootApi api_;
    ScopedManagedRoots* previous_;
    std::vector<Root> roots_;
    inline static thread_local ScopedManagedRoots* current_{};
};

}  // namespace raid::capture
