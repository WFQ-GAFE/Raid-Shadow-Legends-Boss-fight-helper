#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <utility>

namespace raid::observation {

// Observation errors never suppress, duplicate or retry the actual game call.
// Exceptions thrown by the game call itself keep their original propagation.
template <typename Capture, typename Original, typename Publish>
auto observe_selection_call(Capture capture, Original original, Publish publish) {
    using Before = decltype(capture());
    std::optional<Before> before;
    try { before.emplace(capture()); } catch (...) {}
    auto result = original();
    if (before) {
        try { publish(*before, result); } catch (...) {}
    }
    return result;
}

class SelectionHistory {
public:
    static constexpr std::size_t max_entries = 16;
    static constexpr std::size_t max_bytes = 24576;
    std::uint64_t sequence{};
    std::uint64_t dropped{};
    std::size_t bytes{};
    std::deque<std::string> rows;

    void append(std::string row) {
        ++sequence;
        if (row.size() > max_bytes) { ++dropped; return; }
        rows.push_back(std::move(row));
        bytes += rows.back().size();
        while (rows.size() > max_entries || bytes > max_bytes) {
            bytes -= rows.front().size();
            rows.pop_front();
            ++dropped;
        }
    }

    std::string json(std::size_t row_limit = max_entries) const {
        const std::size_t omitted = rows.size() > row_limit ? rows.size() - row_limit : 0;
        std::string text = "{\"schema\":1,\"publishedCount\":" + std::to_string(sequence) +
            ",\"droppedCount\":" + std::to_string(dropped) + ",\"windowOmittedCount\":" +
            std::to_string(omitted) + ",\"events\":[";
        bool first = true;
        for (std::size_t index = omitted; index < rows.size(); ++index) {
            if (!first) text += ',';
            first = false;
            text += rows[index];
        }
        return text + "]}";
    }
};

}  // namespace raid::observation
