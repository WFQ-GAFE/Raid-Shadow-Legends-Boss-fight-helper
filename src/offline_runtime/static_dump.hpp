#pragma once

#include "managed_runtime.hpp"
#include <cmath>
#include <cstdio>
#include <functional>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>

// Static-data objects as JSON, read field by field through IL2CPP reflection,
// so every name is the game's own. Fields holding a default (null, 0, false,
// "", an empty list or object) are left out; enum values are written by member
// name and every enum met is listed once in enums(). Only fields are read: no
// method of a dumped object is invoked.
class ReflectiveJson {
public:
    ReflectiveJson(const ManagedRuntime& runtime, int max_depth, std::size_t max_items = 2048)
        : rt(runtime), max_depth(max_depth), max_items(max_items) {
        class_get_fields = rt.api<void* (*)(void*, void**)>("il2cpp_class_get_fields");
        field_get_name = rt.api<const char* (*)(void*)>("il2cpp_field_get_name");
        field_get_flags = rt.api<int (*)(void*)>("il2cpp_field_get_flags");
        field_get_offset = rt.api<std::size_t (*)(void*)>("il2cpp_field_get_offset");
        field_get_type = rt.api<const void* (*)(void*)>("il2cpp_field_get_type");
        field_static_get_value = rt.api<void (*)(void*, void*)>("il2cpp_field_static_get_value");
        type_get_type = rt.api<int (*)(const void*)>("il2cpp_type_get_type");
        class_from_type = rt.api<void* (*)(const void*)>("il2cpp_class_from_type");
        class_get_type = rt.api<const void* (*)(void*)>("il2cpp_class_get_type");
        class_get_parent = rt.api<void* (*)(void*)>("il2cpp_class_get_parent");
        class_is_enum = rt.api<bool (*)(void*)>("il2cpp_class_is_enum");
        class_is_valuetype = rt.api<bool (*)(void*)>("il2cpp_class_is_valuetype");
        class_enum_basetype = rt.api<const void* (*)(void*)>("il2cpp_class_enum_basetype");
        class_get_name = rt.api<const char* (*)(void*)>("il2cpp_class_get_name");
        class_get_namespace = rt.api<const char* (*)(void*)>("il2cpp_class_get_namespace");
        class_get_rank = rt.api<int (*)(void*)>("il2cpp_class_get_rank");
        class_get_element_class = rt.api<void* (*)(void*)>("il2cpp_class_get_element_class");
        class_array_element_size = rt.api<int (*)(void*)>("il2cpp_class_array_element_size");
        class_get_field_from_name = rt.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name");
        array_length = rt.api<std::uintptr_t (*)(void*)>("il2cpp_array_length");
        array_header = static_cast<std::size_t>(rt.api<std::uintptr_t (*)()>("il2cpp_array_object_header_size")());
        object_get_class = rt.api<void* (*)(void*)>("il2cpp_object_get_class");
    }

    // The object as JSON ("null" when absent).
    std::string json(void* object) {
        std::string out;
        if (!write_object(out, object, 0, true)) out = "null";
        return out;
    }

    // {"Namespace.Enum":{"value":"Member",...},...} for every enum written so far.
    std::string enums() const {
        std::string out = "{";
        bool first = true;
        for (const auto& [name, members] : enum_names) {
            if (!first) out += ',';
            first = false;
            out += quoted(name) + ":{";
            bool first_member = true;
            for (const auto& [value, member] : members) {
                if (!first_member) out += ',';
                first_member = false;
                out += quoted(std::to_string(value)) + ':' + quoted(member);
            }
            out += '}';
        }
        return out + '}';
    }

    // Instance field names and types of a class and its bases, for discovery.
    std::string describe(void* klass) const {
        std::string out = "[";
        bool first = true;
        for (void* current = klass; current; current = class_get_parent(current)) {
            if (full_name(current) == "System.Object") break;
            void* iterator = nullptr;
            while (void* field = class_get_fields(current, &iterator)) {
                if (field_get_flags(field) & kStatic) continue;
                if (!first) out += ',';
                first = false;
                void* type_class = class_from_type(field_get_type(field));
                out += "[" + quoted(field_get_name(field)) + ',' + quoted(type_class ? full_name(type_class) : "?") + ']';
            }
        }
        return out + ']';
    }

    std::size_t truncated = 0;

    static std::string quoted(const std::string& text) {
        std::string out = "\"";
        for (unsigned char c : text) {
            if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
            else if (c < 0x20) {
                char escaped[8];
                std::snprintf(escaped, sizeof(escaped), "\\u%04x", c);
                out += escaped;
            } else out += static_cast<char>(c);
        }
        return out + '"';
    }

private:
    static constexpr int kStatic = 0x0010;
    static constexpr int kLiteral = 0x0040;
    static constexpr std::size_t kObjectHeader = 16;

    const ManagedRuntime& rt;
    int max_depth;
    std::size_t max_items;
    std::unordered_set<void*> active;
    std::map<std::string, std::map<long long, std::string>> enum_names;
    std::unordered_map<void*, const std::map<long long, std::string>*> enum_cache;

    void* (*class_get_fields)(void*, void**);
    const char* (*field_get_name)(void*);
    int (*field_get_flags)(void*);
    std::size_t (*field_get_offset)(void*);
    const void* (*field_get_type)(void*);
    void (*field_static_get_value)(void*, void*);
    int (*type_get_type)(const void*);
    void* (*class_from_type)(const void*);
    const void* (*class_get_type)(void*);
    void* (*class_get_parent)(void*);
    bool (*class_is_enum)(void*);
    bool (*class_is_valuetype)(void*);
    const void* (*class_enum_basetype)(void*);
    const char* (*class_get_name)(void*);
    const char* (*class_get_namespace)(void*);
    int (*class_get_rank)(void*);
    void* (*class_get_element_class)(void*);
    int (*class_array_element_size)(void*);
    void* (*class_get_field_from_name)(void*, const char*);
    std::uintptr_t (*array_length)(void*);
    std::size_t array_header;
    void* (*object_get_class)(void*);

    std::string full_name(void* klass) const {
        const char* space = class_get_namespace(klass);
        const char* name = class_get_name(klass);
        std::string out = space && *space ? std::string(space) + "." : std::string();
        return out + (name ? name : "?");
    }

    template <typename T> static T read(const unsigned char* address) {
        T value{};
        std::memcpy(&value, address, sizeof(T));
        return value;
    }

    static std::string number(double value) {
        if (!std::isfinite(value)) return "null";
        std::ostringstream out;
        out.precision(10);
        out << value;
        return out.str();
    }

    const std::map<long long, std::string>& enum_members(void* klass) {
        auto found = enum_cache.find(klass);
        if (found != enum_cache.end()) return *found->second;
        auto& members = enum_names[full_name(klass)];
        const int base_kind = type_get_type(class_enum_basetype(klass));
        void* iterator = nullptr;
        while (void* field = class_get_fields(klass, &iterator)) {
            if (!(field_get_flags(field) & kLiteral)) continue;
            unsigned char storage[8]{};
            field_static_get_value(field, storage);
            members.emplace(integer(storage, base_kind), field_get_name(field));
        }
        enum_cache[klass] = &members;
        return members;
    }

    static long long integer(const unsigned char* address, int kind) {
        switch (kind) {
        case 0x02: case 0x05: return read<std::uint8_t>(address);
        case 0x04: return read<std::int8_t>(address);
        case 0x03: case 0x07: return read<std::uint16_t>(address);
        case 0x06: return read<std::int16_t>(address);
        case 0x08: return read<std::int32_t>(address);
        case 0x09: return read<std::uint32_t>(address);
        case 0x0a: case 0x0b: return read<std::int64_t>(address);
        default: return 0;
        }
    }

    // A value stored inline at address. Returns false (writing nothing) for a
    // default the caller may leave out; with keep, defaults are written too.
    bool write_value(std::string& out, const unsigned char* address, const void* type, int depth, bool keep) {
        const int kind = type_get_type(type);
        switch (kind) {
        case 0x02: {
            const bool value = read<std::uint8_t>(address) != 0;
            if (!value && !keep) return false;
            out += value ? "true" : "false";
            return true;
        }
        case 0x03: case 0x04: case 0x05: case 0x06: case 0x07: case 0x08: case 0x09: case 0x0a: case 0x0b: {
            const long long value = integer(address, kind);
            if (!value && !keep) return false;
            out += kind == 0x0b ? std::to_string(read<std::uint64_t>(address)) : std::to_string(value);
            return true;
        }
        case 0x0c: case 0x0d: {
            const double value = kind == 0x0c ? read<float>(address) : read<double>(address);
            if (value == 0.0 && !keep) return false;
            out += number(value);
            return true;
        }
        case 0x11: case 0x15: {
            void* klass = class_from_type(type);
            if (!klass) return false;
            if (class_is_valuetype(klass)) {
                if (class_is_enum(klass)) {
                    const int base_kind = type_get_type(class_enum_basetype(klass));
                    const long long value = integer(address, base_kind);
                    if (!value && !keep) { enum_members(klass); return false; }
                    const auto& members = enum_members(klass);
                    const auto member = members.find(value);
                    out += member != members.end() ? quoted(member->second) : std::to_string(value);
                    return true;
                }
                return write_fields(out, klass, address, true, depth, keep);
            }
            return write_object(out, read<void*>(address), depth, keep);
        }
        case 0x0e: case 0x12: case 0x14: case 0x1c: case 0x1d:
            return write_object(out, read<void*>(address), depth, keep);
        default:
            if (keep) out += "null";
            return keep;
        }
    }

    // Instance fields of klass at base: an object (fields at base + offset) or
    // an inline struct (offsets count the object header the struct lacks).
    bool write_fields(std::string& out, void* klass, const unsigned char* base, bool inline_struct, int depth, bool keep) {
        if (depth >= max_depth) {
            ++truncated;
            out += quoted("<depth " + full_name(klass) + ">");
            return true;
        }
        std::string body;
        for (void* current = klass; current; current = class_get_parent(current)) {
            const std::string name = full_name(current);
            if (name == "System.Object" || name == "System.ValueType") break;
            void* iterator = nullptr;
            while (void* field = class_get_fields(current, &iterator)) {
                if (field_get_flags(field) & kStatic) continue;
                const std::size_t offset = field_get_offset(field);
                if (inline_struct && offset < kObjectHeader) continue;
                const unsigned char* address = base + offset - (inline_struct ? kObjectHeader : 0);
                std::string value;
                if (!write_value(value, address, field_get_type(field), depth + 1, false)) continue;
                if (!body.empty()) body += ',';
                body += quoted(field_get_name(field)) + ':' + value;
            }
        }
        if (body.empty() && !keep) return false;
        out += '{' + body + '}';
        return true;
    }

    bool write_elements(std::string& out, void* array, std::size_t count, int depth) {
        void* array_class = object_get_class(array);
        void* element = class_get_element_class(array_class);
        // il2cpp_class_array_element_size takes the element class.
        const std::size_t size = static_cast<std::size_t>(class_array_element_size(element));
        const std::size_t length = static_cast<std::size_t>(array_length(array));
        if (count > length) count = length;
        if (count > max_items) { ++truncated; count = max_items; }
        const auto* data = static_cast<const unsigned char*>(array) + array_header;
        const void* type = class_get_type(element);
        out += '[';
        for (std::size_t index = 0; index < count; ++index) {
            if (index) out += ',';
            write_value(out, data + index * size, type, depth + 1, true);
        }
        out += ']';
        return true;
    }

    bool write_object(std::string& out, void* object, int depth, bool keep) {
        if (!object) {
            if (keep) out += "null";
            return keep;
        }
        void* klass = object_get_class(object);
        const std::string name = full_name(klass);
        if (name == "System.String") {
            const std::string text = rt.string_value(object);
            if (text.empty() && !keep) return false;
            out += quoted(text);
            return true;
        }
        if (class_is_valuetype(klass))
            return write_value(out, static_cast<unsigned char*>(object) + kObjectHeader, class_get_type(klass), depth, keep);
        for (void* current = klass; current; current = class_get_parent(current))
            if (full_name(current) == "System.Delegate") return false;
        if (depth >= max_depth) {
            ++truncated;
            out += quoted("<depth " + name + ">");
            return true;
        }
        if (!active.insert(object).second) {
            out += quoted("<cycle " + name + ">");
            return true;
        }
        struct Leave { std::unordered_set<void*>& set; void* item; ~Leave() { set.erase(item); } } leave{active, object};
        if (class_get_rank(klass) > 0) {
            const std::size_t length = static_cast<std::size_t>(array_length(object));
            if (!length && !keep) return false;
            return write_elements(out, object, length, depth);
        }
        const auto field_of = [&](const char* field_name) { return class_get_field_from_name(klass, field_name); };
        if (name.rfind("System.Collections.Generic.List`1", 0) == 0) {
            void* items_field = field_of("_items");
            void* size_field = field_of("_size");
            if (items_field && size_field) {
                const auto* base = static_cast<const unsigned char*>(object);
                void* items = read<void*>(base + field_get_offset(items_field));
                const int size = read<std::int32_t>(base + field_get_offset(size_field));
                if ((!items || size <= 0) && !keep) return false;
                if (!items || size <= 0) { out += "[]"; return true; }
                return write_elements(out, items, static_cast<std::size_t>(size), depth);
            }
        }
        if (name.rfind("System.Collections.Generic.Dictionary`2", 0) == 0) {
            void* entries_field = field_of("_entries");
            void* count_field = field_of("_count");
            if (entries_field && count_field) {
                const auto* base = static_cast<const unsigned char*>(object);
                void* entries = read<void*>(base + field_get_offset(entries_field));
                const int count = read<std::int32_t>(base + field_get_offset(count_field));
                if ((!entries || count <= 0) && !keep) return false;
                out += '[';
                if (entries && count > 0) {
                    void* entries_class = object_get_class(entries);
                    void* entry = class_get_element_class(entries_class);
                    const std::size_t size = static_cast<std::size_t>(class_array_element_size(entry));
                    void* hash = class_get_field_from_name(entry, "hashCode");
                    void* key = class_get_field_from_name(entry, "key");
                    void* value = class_get_field_from_name(entry, "value");
                    const auto* data = static_cast<const unsigned char*>(entries) + array_header;
                    std::size_t written = 0;
                    for (int index = 0; index < count && hash && key && value; ++index) {
                        const unsigned char* item = data + index * size;
                        if (read<std::int32_t>(item + field_get_offset(hash) - kObjectHeader) < 0) continue;
                        if (written == max_items) { ++truncated; break; }
                        if (written++) out += ',';
                        out += '[';
                        write_value(out, item + field_get_offset(key) - kObjectHeader, field_get_type(key), depth + 1, true);
                        out += ',';
                        write_value(out, item + field_get_offset(value) - kObjectHeader, field_get_type(value), depth + 1, true);
                        out += ']';
                    }
                }
                out += ']';
                return true;
            }
        }
        return write_fields(out, klass, static_cast<const unsigned char*>(object), false, depth, keep);
    }
};
