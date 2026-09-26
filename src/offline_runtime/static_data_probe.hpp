#pragma once

#include "managed_runtime.hpp"
#include <filesystem>
#include <fstream>
#include <functional>
#include <cstring>
#include <vector>

inline void configure_original_messagepack(const ManagedRuntime& model, void* domain) {
    ManagedRuntime generated(model.library, domain, "Unity.RaidApp.dll");
    // Use the game's own initialization. Its resolver order includes native
    // DateTime encoding, which differs from standard MessagePack timestamps.
    generated.invoke(generated.method(generated.klass("Client.RaidApp", "MessagePack"), "Init", 0), nullptr);
}

inline std::vector<unsigned char> read_bounded_input(const std::filesystem::path& path) {
    auto size = std::filesystem::file_size(path);
    if (size == 0 || size > 32 * 1024 * 1024) throw std::runtime_error("MessagePack input size outside probe bound");
    std::vector<unsigned char> contents(size);
    std::ifstream file(path, std::ios::binary);
    file.read(reinterpret_cast<char*>(contents.data()), static_cast<std::streamsize>(size));
    if (!file) throw std::runtime_error("Cannot read copied MessagePack input");
    return contents;
}

inline void* unpack_messagepack(const ManagedRuntime& model, void* domain, const std::filesystem::path& path,
                               void* target_type) {
    ManagedRuntime common(model.library, domain, "Unity.Plarium.Common.dll");
    ManagedRuntime system(model.library, domain, "mscorlib.dll");
    auto contents = read_bounded_input(path);
    void* bytes = system.keep(system.api<void* (*)(void*, std::uintptr_t)>("il2cpp_array_new")(system.klass("System", "Byte"), contents.size()));
    if (!bytes) throw std::runtime_error("MessagePack byte array allocation failed");
    std::memcpy(static_cast<unsigned char*>(bytes) + 32, contents.data(), contents.size());
    void* extensions = common.klass("Plarium.Common.Extensions.MessagePack", "MessagePackExtensions");
    void* definition = common.method_exact(extensions, "FromPackedMessagePack", {"System.Byte[]"});
    void* inflated = common.generic_method(definition, extensions, target_type,
                                           system.klass("System", "Type"));
    void* data = model.keep(common.invoke(inflated, nullptr, {bytes}));
    if (!data) throw std::runtime_error("Static data deserializer returned null");
    return data;
}

inline void* roundtrip_packed_messagepack(const ManagedRuntime& model, void* domain, void* value,
                                           std::size_t* packed_size,
                                           std::vector<unsigned char>* packed_copy = nullptr) {
    ManagedRuntime common(model.library, domain, "Unity.Plarium.Common.dll");
    ManagedRuntime system(model.library, domain, "mscorlib.dll");
    void* extensions = common.klass("Plarium.Common.Extensions.MessagePack", "MessagePackExtensions");
    void* owner_type = model.object_class(value);
    void* pack_definition = common.method_exact(extensions, "ToPackedMessagePack", {"T"});
    void* pack = common.generic_method(pack_definition, extensions, owner_type, system.klass("System", "Type"));
    void* bytes = common.invoke(pack, nullptr, {value});
    if (!bytes) throw std::runtime_error("Original packed MessagePack serializer returned null");
    auto length = system.api<std::uintptr_t (*)(void*)>("il2cpp_array_length")(bytes);
    if (!length || length > 32 * 1024 * 1024) throw std::runtime_error("Serialized MessagePack input size outside bound");
    if (packed_size) *packed_size = static_cast<std::size_t>(length);
    if (packed_copy) {
        const auto offset = system.api<std::uintptr_t (*)()>("il2cpp_array_object_header_size")();
        if (!offset) throw std::runtime_error("Serialized MessagePack array layout unavailable");
        const auto* data = static_cast<const unsigned char*>(bytes) + offset;
        packed_copy->assign(data, data + length);
    }
    void* unpack_definition = common.method_exact(extensions, "FromPackedMessagePack", {"System.Byte[]"});
    void* unpack = common.generic_method(unpack_definition, extensions, owner_type, system.klass("System", "Type"));
    // Keep the restored object in the caller's long-lived root set. The local
    // Unity.Plarium.Common wrapper releases its own temporary handles on return.
    void* restored = model.keep(common.invoke(unpack, nullptr, {bytes}));
    if (!restored || model.object_class(restored) != owner_type)
        throw std::runtime_error("Original packed MessagePack round trip returned the wrong model type");
    return restored;
}

inline void* load_static_data(const ManagedRuntime& model, void* domain, const std::filesystem::path& path,
                             const std::function<void(const char*)>& checkpoint) {
    ManagedRuntime common(model.library, domain, "Unity.Plarium.Common.dll");
    checkpoint("prepare_serialization_resolvers");
    configure_original_messagepack(model, domain);
    checkpoint("read_copied_static_cache");
    void* data = unpack_messagepack(model, domain, path, model.klass("SharedModel.Meta", "ClientStaticData"));
    checkpoint("cache_static_definitions");
    void* manager = model.klass("SharedModel", "SharedModelManager");
    model.invoke(model.method(manager, "SetStaticData", 1), nullptr, {data});
    if (model.get_static<void*>(manager, "StaticData") != data)
        throw std::runtime_error("Private static-data installation did not retain the input");
    return data;
}
