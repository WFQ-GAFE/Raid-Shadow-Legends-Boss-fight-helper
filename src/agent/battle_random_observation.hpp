#pragma once

#include <array>
#include <cstdint>

namespace raid::observation {

struct RandomFrame {
    std::uintptr_t context{};
    std::uintptr_t state{};
    std::uintptr_t random{};
    std::int32_t turn{};
    std::int32_t player_turn{};
    std::int32_t seed{};
    bool seed_available{};
    std::array<std::uint32_t, 4> words{};
    bool setup_id_available{};
    std::array<std::uint8_t, 16> setup_id{};

    bool operator==(const RandomFrame&) const = default;
};

struct RandomObservation {
    bool available{};
    const char* reason{"fields_unavailable"};
    RandomFrame frame{};
};

// Both samples are field reads supplied by the caller. No game method or RNG
// advancement belongs here. Equality rejects a torn read; it is not a claim
// that all battle objects have been atomically captured.
template <typename ReadFrame>
RandomObservation observe_random(ReadFrame read) {
    for (int attempt = 0; attempt < 3; ++attempt) {
        RandomFrame before{}, after{};
        if (!read(before) || !read(after)) return {};
        if (before == after) {
            if (after.words == std::array<std::uint32_t, 4>{}) {
                return {false, "all_zero_state", {}};
            }
            return {true, "stable_double_read", after};
        }
    }
    return {false, "changed_during_read", {}};
}

}  // namespace raid::observation
