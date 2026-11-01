#pragma once

// A complete, independent copy of a running original battle, so a policy can
// try "what happens if" without touching the battle itself.
//
// The copy is a deep copy of the IL2CPP object graph below the BattleProcessor.
// Objects reachable from the shared roots (static data, the battle setup, the
// global game parameters, the journal) are never copied: the engine only reads
// them during a battle. Everything else (state, heroes, effects, skills, RNG,
// statistics, the record) is copied field by field, keeping the graph's shape.
//
// Research 2026-09-25 (docs/chimera-offline-simulation-research.md): a copy of
// 43k-135k objects takes 31-108 ms, every copied slot holds the copy of the
// original's object (structure check: 0 problems), playing 150 actions on a copy
// leaves the original unchanged, and a copy and the original stay identical for
// 600 actions in lockstep. The caller still guards every use: the original's
// CalcHash must be unchanged after the copy has been played.

#include "managed_runtime.hpp"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

// Reads obj->klass and touches the class name under SEH; null if not an object.
inline void* fork_object_class(void* object, const char* (*class_name)(void*)) {
    __try {
        void* klass = *reinterpret_cast<void**>(object);
        if (!klass) return nullptr;
        const char* name = class_name(klass);
        if (!name || !name[0]) return nullptr;
        volatile char first = name[0];
        (void)first;
        return klass;
    } __except (1) {
        return nullptr;
    }
}

class BattleCloner {
public:
    explicit BattleCloner(const ManagedRuntime& model) : model_(model) {
        class_from_type_ = model.api<void* (*)(void*)>("il2cpp_class_from_type");
        type_get_type_ = model.api<int (*)(void*)>("il2cpp_type_get_type");
        is_valuetype_ = model.api<bool (*)(void*)>("il2cpp_class_is_valuetype");
        is_enum_ = model.api<bool (*)(void*)>("il2cpp_class_is_enum");
        get_fields_ = model.api<void* (*)(void*, void**)>("il2cpp_class_get_fields");
        field_flags_ = model.api<int (*)(void*)>("il2cpp_field_get_flags");
        field_offset_ = model.api<std::size_t (*)(void*)>("il2cpp_field_get_offset");
        field_type_ = model.api<void* (*)(void*)>("il2cpp_field_get_type");
        parent_ = model.api<void* (*)(void*)>("il2cpp_class_get_parent");
        instance_size_ = model.api<std::int32_t (*)(void*)>("il2cpp_class_instance_size");
        element_class_ = model.api<void* (*)(void*)>("il2cpp_class_get_element_class");
        array_elem_size_ = model.api<int (*)(void*)>("il2cpp_class_array_element_size");
        array_length_ = model.api<std::uintptr_t (*)(void*)>("il2cpp_array_length");
        array_new_specific_ = model.api<void* (*)(void*, std::uintptr_t)>("il2cpp_array_new_specific");
        object_new_ = model.api<void* (*)(void*)>("il2cpp_object_new");
        object_class_ = model.api<void* (*)(void*)>("il2cpp_object_get_class");
        class_name_ = model.api<const char* (*)(void*)>("il2cpp_class_get_name");
        class_namespace_ = model.api<const char* (*)(void*)>("il2cpp_class_get_namespace");
        rank_ = model.api<int (*)(void*)>("il2cpp_class_get_rank");
        gc_disable_ = model.api<void (*)()>("il2cpp_gc_disable");
        gc_enable_ = model.api<void (*)()>("il2cpp_gc_enable");
    }

    // Everything reachable from these objects is read-only for a battle and is
    // shared by the copies. Returns how many objects are shared so far.
    std::size_t share(const std::vector<void*>& roots) {
        std::vector<void*> stack(roots.begin(), roots.end());
        while (!stack.empty()) {
            void* object = stack.back();
            stack.pop_back();
            if (!object || !shared_.insert(object).second) continue;
            visit_refs(object, [&](void** slot) {
                if (*slot) stack.push_back(*slot);
            });
        }
        return shared_.size();
    }

    // A rooted deep copy of root. The GC must not move or collect objects while
    // the graph is half copied, so it is disabled for the copy.
    // Objects the next copy shares instead of copying (with all they reach), for parts
    // of the battle that are never written again: a finished turn's statistics.
    // Applies to the next clone() only.
    void borrow_next(const std::vector<void*>& objects) {
        borrowed_.clear();
        borrowed_.insert(objects.begin(), objects.end());
    }
    std::size_t borrowed() const { return borrowed_.size(); }

    void* clone(void* root) {
        map_.clear();
        work_.clear();
        bad_count_ = 0;
        gc_disable_();
        try {
            void* result = copy_one(root);
            while (!work_.empty()) {
                void* copy = work_.back();
                work_.pop_back();
                visit_refs(copy, [&](void** slot) { *slot = copy_one(*slot); });
            }
            model_.keep(result);
            gc_enable_();
            if (bad_count_) throw std::runtime_error("Battle copy met a slot that is not an object");
            return result;
        } catch (...) {
            gc_enable_();
            throw;
        }
    }

    std::size_t copied() const { return map_.size(); }
    std::size_t shared() const { return shared_.size(); }

    // A digest of an object graph's private part (it stops at shared objects): each
    // object's class and raw contents, so any change below root changes it.
    std::uint64_t digest(void* root) {
        std::unordered_set<void*> seen;
        std::vector<void*> stack{root};
        std::uint64_t hash = 1469598103934665603ULL;
        auto mix = [&](const unsigned char* bytes, std::size_t size) {
            for (std::size_t i = 0; i < size; ++i) {
                hash ^= bytes[i];
                hash *= 1099511628211ULL;
            }
        };
        while (!stack.empty()) {
            void* object = stack.back();
            stack.pop_back();
            if (!object || shared_.count(object) || !seen.insert(object).second) continue;
            void* klass = object_class_(object);
            const auto& ci = info(klass);
            mix(reinterpret_cast<const unsigned char*>(&klass), sizeof(klass));
            if (ci.is_string) continue;
            const std::size_t size = ci.is_array ? 32 + array_length_(object) * ci.elem_size : ci.instance_size;
            mix(static_cast<const unsigned char*>(object) + 16, size - 16);
            visit_refs(object, [&](void** slot) { if (*slot) stack.push_back(*slot); });
        }
        return hash;
    }

    // The most frequent classes of the last copy, "Namespace.Class:count" (for tuning).
    std::vector<std::string> largest_classes(std::size_t limit) {
        std::unordered_map<std::string, std::size_t> counts;
        for (const auto& [original, copy] : map_) ++counts[info(object_class_(original)).name];
        std::vector<std::pair<std::string, std::size_t>> sorted(counts.begin(), counts.end());
        std::sort(sorted.begin(), sorted.end(), [](const auto& a, const auto& b) { return a.second > b.second; });
        std::vector<std::string> result;
        for (std::size_t i = 0; i < sorted.size() && i < limit; ++i)
            result.push_back(sorted[i].first + ":" + std::to_string(sorted[i].second));
        return result;
    }

    // Structural check of the last copy: each copied slot holds the copy (or the
    // shared original) of the original slot's object. Empty when sound.
    std::vector<std::string> verify_last(std::size_t limit = 20) {
        std::vector<std::string> problems;
        for (auto& [original, copy] : map_) {
            if (object_class_(original) != object_class_(copy)) {
                problems.push_back("class mismatch");
                continue;
            }
            std::vector<void**> before, after;
            slots(original, before);
            slots(copy, after);
            if (before.size() != after.size()) {
                problems.push_back(info(object_class_(original)).name + " slot count");
                continue;
            }
            for (std::size_t i = 0; i < before.size() && problems.size() < limit; ++i) {
                void* value = *before[i];
                void* expected = value;
                if (value && !shared_.count(value)) {
                    const auto found = map_.find(value);
                    if (found != map_.end()) expected = found->second;
                }
                if (*after[i] != expected) problems.push_back(info(object_class_(original)).name + " slot");
            }
            if (problems.size() >= limit) break;
        }
        return problems;
    }

private:
    struct ClassInfo {
        bool is_array{};
        bool is_string{};
        bool elem_is_ref{};
        std::size_t elem_size{};
        std::size_t instance_size{};
        std::vector<std::uint32_t> refs;  // objects: absolute offsets; struct arrays: per element
        std::string name;
    };

    const ClassInfo& info(void* klass) {
        const auto found = infos_.find(klass);
        if (found != infos_.end()) return found->second;
        ClassInfo ci;
        ci.name = std::string(class_namespace_(klass)) + "." + class_name_(klass);
        if (ci.name == "System.String") {
            ci.is_string = true;
        } else if (rank_(klass) > 0) {
            ci.is_array = true;
            void* element = element_class_(klass);
            ci.elem_size = static_cast<std::size_t>(array_elem_size_(element));
            ci.elem_is_ref = !is_valuetype_(element);
            if (!ci.elem_is_ref) struct_refs(element, 0, ci.refs);
        } else {
            ci.instance_size = static_cast<std::size_t>(instance_size_(klass));
            object_refs(klass, ci.refs);
        }
        return infos_.emplace(klass, std::move(ci)).first->second;
    }

    template <typename F> void visit_refs(void* object, F&& visit) {
        void* klass = object_class_(object);
        const auto& ci = info(klass);
        auto* base = static_cast<unsigned char*>(object);
        auto checked = [&](void** slot) {
            void* child = *slot;
            if (!child) return;
            if (!valid_.count(child)) {
                if (!fork_object_class(child, class_name_)) {
                    ++bad_count_;
                    return;
                }
                valid_.insert(child);
            }
            visit(slot);
        };
        if (ci.is_string) return;
        if (ci.is_array) {
            const auto length = array_length_(object);
            auto* data = base + 32;
            if (ci.elem_is_ref) {
                for (std::uintptr_t i = 0; i < length; ++i) checked(reinterpret_cast<void**>(data + i * 8));
            } else if (!ci.refs.empty()) {
                for (std::uintptr_t i = 0; i < length; ++i)
                    for (auto offset : ci.refs) checked(reinterpret_cast<void**>(data + i * ci.elem_size + offset));
            }
            return;
        }
        for (auto offset : ci.refs) checked(reinterpret_cast<void**>(base + offset));
    }

    void* copy_one(void* object) {
        if (!object || shared_.count(object)) return object;
        const auto found = map_.find(object);
        if (found != map_.end()) return found->second;
        if (borrowed_.count(object)) return object;  // shared with the original, not visited
        void* klass = object_class_(object);
        const auto& ci = info(klass);
        if (ci.is_string) return object;  // strings are immutable
        void* copy;
        if (ci.is_array) {
            if (*reinterpret_cast<void**>(static_cast<unsigned char*>(object) + 16))
                throw std::runtime_error("Multi-dimensional array in the battle graph: " + ci.name);
            const auto length = array_length_(object);
            copy = array_new_specific_(klass, length);
            if (!copy) throw std::runtime_error("Allocation failed while copying the battle: " + ci.name);
            std::memcpy(static_cast<unsigned char*>(copy) + 32, static_cast<unsigned char*>(object) + 32,
                        length * ci.elem_size);
        } else {
            copy = object_new_(klass);
            if (!copy) throw std::runtime_error("Allocation failed while copying the battle: " + ci.name);
            std::memcpy(static_cast<unsigned char*>(copy) + 16, static_cast<unsigned char*>(object) + 16,
                        ci.instance_size - 16);
        }
        map_.emplace(object, copy);
        work_.push_back(copy);
        return copy;
    }

    bool pointer_type(void* type) const {
        const int kind = type_get_type_(type);
        return kind == 0x0f /*PTR*/ || kind == 0x1b /*FNPTR*/ || kind == 0x18 /*I*/ || kind == 0x19 /*U*/;
    }

    void object_refs(void* klass, std::vector<std::uint32_t>& out) {
        for (void* k = klass; k; k = parent_(k)) {
            void* iterator = nullptr;
            while (void* field = get_fields_(k, &iterator)) {
                if (field_flags_(field) & 0x10) continue;  // static
                void* type = field_type_(field);
                if (pointer_type(type)) continue;
                void* field_class = class_from_type_(type);
                if (!field_class) continue;
                const auto offset = static_cast<std::uint32_t>(field_offset_(field));
                if (!is_valuetype_(field_class)) out.push_back(offset);
                else if (!is_enum_(field_class)) struct_refs(field_class, offset, out);
            }
        }
    }

    // Struct fields report boxed offsets (object header included).
    void struct_refs(void* klass, std::uint32_t base, std::vector<std::uint32_t>& out) {
        void* iterator = nullptr;
        while (void* field = get_fields_(klass, &iterator)) {
            if (field_flags_(field) & 0x10) continue;
            void* type = field_type_(field);
            if (pointer_type(type)) continue;
            void* field_class = class_from_type_(type);
            if (!field_class || field_class == klass) continue;
            const auto offset = base + static_cast<std::uint32_t>(field_offset_(field)) - 16;
            if (!is_valuetype_(field_class)) out.push_back(offset);
            else if (!is_enum_(field_class)) struct_refs(field_class, offset, out);
        }
    }

    void slots(void* object, std::vector<void**>& out) {
        const auto& ci = info(object_class_(object));
        auto* base = static_cast<unsigned char*>(object);
        if (ci.is_string) return;
        if (ci.is_array) {
            const auto length = array_length_(object);
            auto* data = base + 32;
            if (ci.elem_is_ref)
                for (std::uintptr_t i = 0; i < length; ++i) out.push_back(reinterpret_cast<void**>(data + i * 8));
            else
                for (std::uintptr_t i = 0; i < length; ++i)
                    for (auto offset : ci.refs) out.push_back(reinterpret_cast<void**>(data + i * ci.elem_size + offset));
            return;
        }
        for (auto offset : ci.refs) out.push_back(reinterpret_cast<void**>(base + offset));
    }

    const ManagedRuntime& model_;
    std::unordered_set<void*> shared_;
    std::unordered_set<void*> valid_;
    std::unordered_map<void*, void*> map_;
    std::vector<void*> work_;
    std::unordered_map<void*, ClassInfo> infos_;
    std::size_t bad_count_{};
    std::unordered_set<void*> borrowed_;
    void* (*class_from_type_)(void*);
    int (*type_get_type_)(void*);
    bool (*is_valuetype_)(void*);
    bool (*is_enum_)(void*);
    void* (*get_fields_)(void*, void**);
    int (*field_flags_)(void*);
    std::size_t (*field_offset_)(void*);
    void* (*field_type_)(void*);
    void* (*parent_)(void*);
    std::int32_t (*instance_size_)(void*);
    void* (*element_class_)(void*);
    int (*array_elem_size_)(void*);
    std::uintptr_t (*array_length_)(void*);
    void* (*array_new_specific_)(void*, std::uintptr_t);
    void* (*object_new_)(void*);
    void* (*object_class_)(void*);
    const char* (*class_name_)(void*);
    const char* (*class_namespace_)(void*);
    int (*rank_)(void*);
    void (*gc_disable_)();
    void (*gc_enable_)();
};
