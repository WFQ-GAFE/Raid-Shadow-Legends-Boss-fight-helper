#pragma once

#include "il2cpp_api.hpp"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace raid::capture {

struct PackedObject {
    std::vector<std::uint8_t> bytes;
    std::string error;
    bool ok{};
};

class GcRoot {
public:
    GcRoot(Il2CppApi& api, void* object) : api_(api) {
        if (!api_.gchandle_new || !api_.gchandle_get_target ||
            !api_.gchandle_free || !object) {
            return;
        }
        handle_ = api_.gchandle_new(object, false);
        if (!handle_ || api_.gchandle_get_target(handle_) != object) {
            if (handle_) api_.gchandle_free(handle_);
            handle_ = 0;
        }
    }
    GcRoot(const GcRoot&) = delete;
    GcRoot& operator=(const GcRoot&) = delete;
    ~GcRoot() {
        if (handle_) api_.gchandle_free(handle_);
    }
    bool valid() const { return handle_ != 0; }

private:
    Il2CppApi& api_;
    std::uintptr_t handle_{};
};

inline const Il2CppImage* find_image(Il2CppApi& api, const char* wanted) {
    if (!api.domain_get || !api.domain_get_assemblies ||
        !api.assembly_get_image || !api.image_get_name) {
        return nullptr;
    }
    Il2CppDomain* domain = api.domain_get();
    if (!domain) return nullptr;
    std::size_t count = 0;
    const Il2CppAssembly** assemblies = api.domain_get_assemblies(domain, &count);
    for (std::size_t i = 0; assemblies && i < count; ++i) {
        const Il2CppImage* image = api.assembly_get_image(assemblies[i]);
        const char* name = image ? api.image_get_name(image) : nullptr;
        if (name && std::strcmp(name, wanted) == 0) return image;
    }
    return nullptr;
}

inline const MethodInfo* find_method(Il2CppApi& api, Il2CppClass* klass,
                                    const char* wanted, int parameter_count) {
    for (int depth = 0; klass && depth < 32; ++depth) {
        void* iterator = nullptr;
        while (const MethodInfo* method = api.class_get_methods(klass, &iterator)) {
            const char* name = api.method_get_name(method);
            if (name && std::strcmp(name, wanted) == 0 &&
                api.method_get_param_count(method) ==
                    static_cast<std::uint32_t>(parameter_count)) {
                return method;
            }
        }
        klass = api.class_get_parent(klass);
    }
    return nullptr;
}

inline const MethodInfo* find_generic_value_method(Il2CppApi& api,
                                                   Il2CppClass* klass,
                                                   const char* wanted) {
    for (int depth = 0; klass && depth < 32; ++depth) {
        void* iterator = nullptr;
        while (const MethodInfo* method = api.class_get_methods(klass, &iterator)) {
            const char* name = api.method_get_name(method);
            if (!name || std::strcmp(name, wanted) != 0 ||
                api.method_get_param_count(method) != 1) {
                continue;
            }
            const Il2CppType* parameter = api.method_get_param(method, 0);
            char* parameter_name = parameter ? api.type_get_name(parameter) : nullptr;
            const bool matches = parameter_name &&
                                 std::strcmp(parameter_name, "T") == 0;
            if (parameter_name) api.free(parameter_name);
            if (matches) return method;
        }
        klass = api.class_get_parent(klass);
    }
    return nullptr;
}

inline std::string exception_text(Il2CppApi& api, void* exception) {
    if (!exception || !api.format_exception) return "managed_exception";
    char buffer[2048]{};
    api.format_exception(exception, buffer, static_cast<int>(sizeof(buffer)));
    return buffer[0] ? buffer : "managed_exception";
}

inline void* invoke(Il2CppApi& api, const MethodInfo* method, void* instance,
                    std::vector<void*> arguments, std::string& error) {
    if (!method || !api.runtime_invoke) {
        error = "runtime_invoke_unavailable";
        return nullptr;
    }
    void* exception = nullptr;
    void* result = api.runtime_invoke(
        method, instance, arguments.empty() ? nullptr : arguments.data(),
        &exception);
    if (exception) {
        error = exception_text(api, exception);
        return nullptr;
    }
    return result;
}

inline PackedObject pack_with_game_messagepack(Il2CppApi& api, void* object,
                                               std::size_t max_bytes) {
    PackedObject result{};
    if (!object) {
        result.error = "model_unavailable";
        return result;
    }
    if (!api.class_get_methods || !api.class_get_parent ||
        !api.method_get_name || !api.method_get_param_count ||
        !api.method_get_param || !api.type_get_name || !api.free ||
        !api.method_get_object || !api.method_get_from_reflection ||
        !api.object_get_class || !api.class_get_type || !api.type_get_object ||
        !api.array_new || !api.array_length || !api.array_get_byte_length ||
        !api.array_object_header_size || !api.gc_wbarrier_set_field ||
        !api.gchandle_new ||
        !api.gchandle_get_target || !api.gchandle_free ||
        !api.class_from_name || !api.class_get_method_from_name) {
        result.error = "messagepack_runtime_api_unavailable";
        return result;
    }

    const Il2CppImage* common_image = find_image(api, "Unity.Plarium.Common.dll");
    Il2CppClass* extensions = common_image
        ? api.class_from_name(common_image,
                              "Plarium.Common.Extensions.MessagePack",
                              "MessagePackExtensions")
        : nullptr;
    if (!extensions) {
        result.error = "game_messagepack_extensions_unavailable";
        return result;
    }

    const MethodInfo* pack_definition = find_generic_value_method(
        api, extensions, "ToPackedMessagePack");
    if (!pack_definition) {
        result.error = "game_messagepack_packer_unavailable";
        return result;
    }
    void* reflected_definition = api.method_get_object(pack_definition, extensions);
    GcRoot reflected_root(api, reflected_definition);
    if (!reflected_root.valid()) {
        result.error = "messagepack_reflection_root_failed";
        return result;
    }

    Il2CppClass* system_image_type = nullptr;
    const Il2CppImage* system_image = find_image(api, "mscorlib.dll");
    if (system_image) {
        system_image_type = api.class_from_name(system_image, "System", "Type");
    }
    Il2CppClass* model_type = api.object_get_class(object);
    if (!system_image_type || !model_type) {
        result.error = "messagepack_model_type_unavailable";
        return result;
    }
    const Il2CppType* model_type_info = api.class_get_type(model_type);
    void* model_type_object = model_type_info
        ? api.type_get_object(model_type_info)
        : nullptr;
    GcRoot model_type_root(api, model_type_object);
    if (!model_type_root.valid()) {
        result.error = "messagepack_type_object_failed";
        return result;
    }
    void* type_arguments = api.array_new(system_image_type, 1);
    GcRoot type_arguments_root(api, type_arguments);
    if (!type_arguments_root.valid()) {
        result.error = "messagepack_type_arguments_failed";
        return result;
    }
    const std::uintptr_t array_data_offset = api.array_object_header_size();
    if (!array_data_offset) {
        result.error = "messagepack_array_layout_unavailable";
        return result;
    }
    auto** type_argument_slot = reinterpret_cast<void**>(
        reinterpret_cast<unsigned char*>(type_arguments) + array_data_offset);
    api.gc_wbarrier_set_field(type_arguments, type_argument_slot,
                              model_type_object);

    Il2CppClass* reflected_class = api.object_get_class(reflected_definition);
    const MethodInfo* make_generic = find_method(
        api, reflected_class, "MakeGenericMethod", 1);
    if (!make_generic) {
        result.error = "reflection_make_generic_method_unavailable";
        return result;
    }
    std::string invoke_error;
    void* closed_reflection = invoke(
        api, make_generic, reflected_definition, {type_arguments}, invoke_error);
    GcRoot closed_reflection_root(api, closed_reflection);
    if (!closed_reflection_root.valid()) {
        result.error = invoke_error.empty()
            ? "messagepack_generic_method_failed"
            : std::move(invoke_error);
        return result;
    }
    const MethodInfo* closed_method =
        api.method_get_from_reflection(closed_reflection);
    if (!closed_method) {
        result.error = "messagepack_inflated_method_unavailable";
        return result;
    }
    void* packed_bytes = invoke(api, closed_method, nullptr, {object}, invoke_error);
    GcRoot packed_bytes_root(api, packed_bytes);
    if (!packed_bytes_root.valid()) {
        result.error = invoke_error.empty()
            ? "game_messagepack_returned_null"
            : std::move(invoke_error);
        return result;
    }
    const std::uintptr_t element_count = api.array_length(packed_bytes);
    const std::uintptr_t length = api.array_get_byte_length(packed_bytes);
    if (!length || element_count != length || length > max_bytes) {
        result.error = "packed_messagepack_size_out_of_bound";
        return result;
    }
    const auto data_offset = api.array_object_header_size();
    if (!data_offset) {
        result.error = "packed_messagepack_array_layout_unavailable";
        return result;
    }
    const auto* bytes = reinterpret_cast<const std::uint8_t*>(
        reinterpret_cast<const unsigned char*>(packed_bytes) + data_offset);
    result.bytes.assign(bytes, bytes + length);
    result.ok = true;
    return result;
}

inline std::string base64(const std::vector<std::uint8_t>& bytes) {
    constexpr char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string output;
    output.reserve(((bytes.size() + 2) / 3) * 4);
    for (std::size_t i = 0; i < bytes.size(); i += 3) {
        const std::uint32_t a = bytes[i];
        const std::uint32_t b = i + 1 < bytes.size() ? bytes[i + 1] : 0;
        const std::uint32_t c = i + 2 < bytes.size() ? bytes[i + 2] : 0;
        const std::uint32_t value = (a << 16) | (b << 8) | c;
        output.push_back(alphabet[(value >> 18) & 63]);
        output.push_back(alphabet[(value >> 12) & 63]);
        output.push_back(i + 1 < bytes.size() ? alphabet[(value >> 6) & 63] : '=');
        output.push_back(i + 2 < bytes.size() ? alphabet[value & 63] : '=');
    }
    return output;
}

}  // namespace raid::capture
