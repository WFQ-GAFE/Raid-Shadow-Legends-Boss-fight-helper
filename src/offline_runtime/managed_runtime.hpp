#pragma once

#include <windows.h>
#include <cstdint>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <vector>
#include <type_traits>
#include <cstring>

// Used exclusively after the isolated worker has initialized its own runtime.
// Pointers here never refer to objects in another process.
struct ManagedRuntime {
    HMODULE library;
    void* image{};
    mutable std::vector<std::uintptr_t> roots;
    ManagedRuntime(const ManagedRuntime&) = delete;
    ManagedRuntime& operator=(const ManagedRuntime&) = delete;
    ~ManagedRuntime() {
        auto release = reinterpret_cast<void (*)(std::uintptr_t)>(GetProcAddress(library, "il2cpp_gchandle_free"));
        if (release) for (auto root : roots) release(root);
    }
    void* keep(void* value) const {
        if (value) {
            auto root = api<std::uintptr_t (*)(void*, bool)>("il2cpp_gchandle_new")(value, false);
            if (!root || api<void* (*)(std::uintptr_t)>("il2cpp_gchandle_get_target")(root) != value)
                throw std::runtime_error("Private GC handle round-trip failed");
            roots.push_back(root);
        }
        return value;
    }
    // Long runs must not pin every intermediate object: callers record
    // root_mark() before a bounded step and release everything rooted since.
    // Objects the engine itself still references stay alive through it.
    std::size_t root_mark() const { return roots.size(); }
    void release_roots_since(std::size_t mark) const {
        if (mark > roots.size()) throw std::runtime_error("Invalid private GC root mark");
        auto release = api<void (*)(std::uintptr_t)>("il2cpp_gchandle_free");
        while (roots.size() > mark) {
            release(roots.back());
            roots.pop_back();
        }
    }
    template <typename T> T api(const char* name) const {
        auto result = reinterpret_cast<T>(GetProcAddress(library, name));
        if (!result) throw std::runtime_error(std::string("Missing runtime export: ") + name);
        return result;
    }
    ManagedRuntime(HMODULE module, void* domain, const char* assembly_name = "Unity.SharedModel.dll") : library(module) {
        std::size_t count = 0;
        auto all = api<void** (*)(void*, std::size_t*)>("il2cpp_domain_get_assemblies")(domain, &count);
        for (std::size_t i = 0; i < count; ++i) {
            auto candidate = api<void* (*)(void*)>("il2cpp_assembly_get_image")(all[i]);
            auto name = api<const char* (*)(void*)>("il2cpp_image_get_name")(candidate);
            if (name && std::string(name) == assembly_name) { image = candidate; break; }
        }
        if (!image) throw std::runtime_error("Shared battle model assembly unavailable");
    }
    void* klass(const char* name_space, const char* name) const {
        auto value = api<void* (*)(void*, const char*, const char*)>("il2cpp_class_from_name")(image, name_space, name);
        if (!value) throw std::runtime_error(std::string("Missing class: ") + name_space + "." + name);
        return value;
    }
    void* object_class(void* object) const {
        if (!object) throw std::runtime_error("Null managed object");
        return api<void* (*)(void*)>("il2cpp_object_get_class")(object);
    }
    void* field(void* object, const char* name) const {
        auto value = api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(object_class(object), name);
        if (!value) throw std::runtime_error(std::string("Missing field: ") + name);
        return value;
    }
    void* field_class(void* object, const char* name) const {
        auto type = api<void* (*)(void*)>("il2cpp_field_get_type")(field(object, name));
        return api<void* (*)(void*)>("il2cpp_class_from_type")(type);
    }
    void* method(void* type, const char* name, int arguments) const {
        auto value = api<void* (*)(void*, const char*, int)>("il2cpp_class_get_method_from_name")(type, name, arguments);
        if (!value) throw std::runtime_error(std::string("Missing method: ") + name);
        return value;
    }
    void* method_exact(void* type, const char* name, std::initializer_list<const char*> parameters) const {
        void* iterator = nullptr;
        auto next = api<void* (*)(void*, void**)>("il2cpp_class_get_methods");
        while (void* candidate = next(type, &iterator)) {
            auto method_name = api<const char* (*)(void*)>("il2cpp_method_get_name")(candidate);
            if (std::strcmp(method_name, name) != 0 || api<unsigned (*)(void*)>("il2cpp_method_get_param_count")(candidate) != parameters.size()) continue;
            bool matches = true;
            unsigned index = 0;
            for (const auto expected : parameters) {
                auto param = api<void* (*)(void*, unsigned)>("il2cpp_method_get_param")(candidate, index++);
                char* actual = api<char* (*)(void*)>("il2cpp_type_get_name")(param);
                matches = matches && actual && std::string(actual) == expected;
                api<void (*)(void*)>("il2cpp_free")(actual);
            }
            if (matches) return candidate;
        }
        throw std::runtime_error(std::string("Missing exact overload: ") + name);
    }
    void* type_object(void* type) const {
        auto info = api<void* (*)(void*)>("il2cpp_class_get_type")(type);
        return keep(api<void* (*)(void*)>("il2cpp_type_get_object")(info));
    }
    void* reference_array(void* element_class, const std::vector<void*>& items) const {
        void* array = keep(api<void* (*)(void*, std::uintptr_t)>("il2cpp_array_new")(element_class, items.size()));
        if (!array) throw std::runtime_error("Managed array allocation failed");
        for (std::size_t i = 0; i < items.size(); ++i)
            api<void (*)(void*, void**, void*)>("il2cpp_gc_wbarrier_set_field")(
                array, reinterpret_cast<void**>(static_cast<unsigned char*>(array) + 32) + i, items[i]);
        return array;
    }
    void* generic_method(void* definition, void* declaring_class, void* type_argument, void* system_type_class) const {
        void* reflection = keep(api<void* (*)(void*, void*)>("il2cpp_method_get_object")(definition, declaring_class));
        void* arguments = reference_array(system_type_class, {type_object(type_argument)});
        void* inflated = call(reflection, "MakeGenericMethod", {arguments});
        return api<void* (*)(void*)>("il2cpp_method_get_from_reflection")(inflated);
    }
    template <typename T> T get_static(void* type, const char* name) const {
        api<void (*)(void*)>("il2cpp_runtime_class_init")(type);
        auto info = api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(type, name);
        if (!info) throw std::runtime_error(std::string("Missing static field: ") + name);
        T value{};
        api<void (*)(void*, void*)>("il2cpp_field_static_get_value")(info, &value);
        return value;
    }
    template <typename T> void set_static(void* type, const char* name, T value) const {
        api<void (*)(void*)>("il2cpp_runtime_class_init")(type);
        auto info = api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(type, name);
        if (!info) throw std::runtime_error(std::string("Missing static field: ") + name);
        if constexpr (std::is_pointer_v<T>)
            api<void (*)(void*, void*)>("il2cpp_field_static_set_value")(info, value);
        else
            api<void (*)(void*, void*)>("il2cpp_field_static_set_value")(info, &value);
        if (get_static<T>(type, name) != value) throw std::runtime_error("Private static field write mismatch");
    }
    void* parameter_class(void* method, unsigned index) const {
        auto type = api<void* (*)(void*, unsigned)>("il2cpp_method_get_param")(method, index);
        return api<void* (*)(void*)>("il2cpp_class_from_type")(type);
    }
    std::string string_value(void* object) const {
        if (!object) return {};
        int length = api<int (*)(void*)>("il2cpp_string_length")(object);
        if (length < 0) return {};
        if (length > 2048) length = 2048;
        auto chars = api<const wchar_t* (*)(void*)>("il2cpp_string_chars")(object);
        const int bytes = WideCharToMultiByte(CP_UTF8, 0, chars, length, nullptr, 0, nullptr, nullptr);
        std::string output(bytes, '\0');
        WideCharToMultiByte(CP_UTF8, 0, chars, length, output.data(), bytes, nullptr, nullptr);
        return output;
    }
    void* invoke(void* method, void* object, std::initializer_list<void*> arguments = {}) const {
        if (!method) throw std::runtime_error("Null method for private runtime invocation");
        std::vector<void*> args(arguments);
        void* exception = nullptr;
        auto result = api<void* (*)(void*, void*, void**, void**)>("il2cpp_runtime_invoke")(
            method, object, args.empty() ? nullptr : args.data(), &exception);
        if (exception) {
            keep(exception);
            std::string message = "Managed exception: ";
            char stack[4096]{};
            api<void (*)(void*, char*, int)>("il2cpp_format_stack_trace")(exception, stack, sizeof(stack));
            for (int depth = 0; depth < 5 && exception; ++depth) {
                if (depth) message += " -> ";
                auto name = api<const char* (*)(void*)>("il2cpp_class_get_name")(object_class(exception));
                message += name ? name : "unknown";
                message += ": " + string_value(get<void*>(exception, "_message"));
                exception = get<void*>(exception, "_innerException");
            }
            if (stack[0]) message += std::string(" | stack: ") + stack;
            throw std::runtime_error(message);
        }
        return keep(result);
    }
    void* call(void* object, const char* name, std::initializer_list<void*> args = {}) const {
        return invoke(method(object_class(object), name, static_cast<int>(args.size())), object, args);
    }
    void* allocate(void* type, bool construct = false) const {
        if (!type) throw std::runtime_error("Null class for allocation");
        auto value = api<void* (*)(void*)>("il2cpp_object_new")(type);
        if (!value) {
            auto name = api<const char* (*)(void*)>("il2cpp_class_get_name")(type);
            throw std::runtime_error(std::string("Managed allocation failed: ") + (name ? name : "unknown"));
        }
        keep(value);
        if (construct) call(value, ".ctor");
        return value;
    }
    void* allocate(const char* name_space, const char* name, bool construct = false) const {
        return allocate(klass(name_space, name), construct);
    }
    template <typename T> void set(void* object, const char* name, T value) const {
        if constexpr (std::is_pointer_v<T>)
            api<void (*)(void*, void*, void*)>("il2cpp_field_set_value_object")(object, field(object, name), value);
        else
            api<void (*)(void*, void*, void*)>("il2cpp_field_set_value")(object, field(object, name), &value);
        if (get<T>(object, name) != value) throw std::runtime_error(std::string("Private field write mismatch: ") + name);
    }
    template <typename T> T get(void* object, const char* name) const {
        T value{};
        api<void (*)(void*, void*, void*)>("il2cpp_field_get_value")(object, field(object, name), &value);
        return value;
    }
    template <typename T> T unbox(void* object) const {
        auto value = api<void* (*)(void*)>("il2cpp_object_unbox")(object);
        if (!value) throw std::runtime_error("Missing boxed value");
        return *static_cast<T*>(value);
    }
};
