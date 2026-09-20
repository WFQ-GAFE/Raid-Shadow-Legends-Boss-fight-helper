#pragma once
#include <deque>
#include <string>
#include <utility>
#include <cstdint>

// Fits inside the 16 KiB lifecycle slot together with its ordinary payload.
struct EventHistory {
    std::deque<std::string> events;
    std::size_t bytes{};
    std::uint64_t sequence{};
    std::string last_state;
    void append_state(const std::string& state, std::uint64_t tick) {
        if (state == last_state) return;
        last_state = state;
        append("{\"sequence\":" + std::to_string(++sequence) +
            ",\"observedAtTick\":" + std::to_string(tick) + "," + state + "}");
    }
    void append(std::string event) {
        bytes += event.size();
        events.push_back(std::move(event));
        while (!events.empty() && (events.size() > 32 || bytes > 8192)) {
            bytes -= events.front().size();
            events.pop_front();
        }
    }
    std::string json() const {
        std::string output = "[";
        for (const auto& event : events) {
            if (output.size() > 1) output += ',';
            output += event;
        }
        return output + ']';
    }
};
