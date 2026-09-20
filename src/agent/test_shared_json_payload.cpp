#include "shared_json_payload.hpp"
#include <iostream>
#include <stdexcept>

int main() {
    const auto error = shared_json_overflow(262156, 262144);
    if (error.size() >= 4096 || error.find("262156") == std::string::npos ||
        error != "{\"type\":\"ipc_error\",\"error\":\"payload_too_large\",\"requiredBytes\":262156,\"capacity\":262144}") {
        throw std::runtime_error("Invalid overflow frame");
    }
    std::cout << error << '\n';
}
