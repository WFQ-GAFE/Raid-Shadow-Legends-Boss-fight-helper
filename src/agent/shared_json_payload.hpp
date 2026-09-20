#pragma once
#include <cstddef>
#include <string>

inline std::string shared_json_overflow(std::size_t required, std::size_t capacity) {
    return "{\"type\":\"ipc_error\",\"error\":\"payload_too_large\",\"requiredBytes\":" +
        std::to_string(required) + ",\"capacity\":" + std::to_string(capacity) + "}";
}
