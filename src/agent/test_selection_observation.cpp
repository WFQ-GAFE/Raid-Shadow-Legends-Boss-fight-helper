#include "selection_observation.hpp"

#include <cassert>
#include <iostream>
#include <stdexcept>

int main() {
    using namespace raid::observation;
    int original_calls = 0, published = 0;
    auto original = [&]() { ++original_calls; return 123; };
    auto publish = [&](int before, int result) { assert(before == 7 && result == 123); ++published; };
    assert(observe_selection_call([]() { return 7; }, original, publish) == 123);
    assert(original_calls == 1 && published == 1);
    assert(observe_selection_call([]() -> int { throw std::runtime_error("read failure"); }, original, publish) == 123);
    assert(original_calls == 2 && published == 1);
    assert(observe_selection_call([]() { return 7; }, original, [](int, int) { throw std::runtime_error("log failure"); }) == 123);
    assert(original_calls == 3);
    try {
        observe_selection_call([]() { return 7; }, [&]() -> int { ++original_calls; throw std::runtime_error("game exception"); }, publish);
        assert(false);
    } catch (const std::runtime_error&) {}
    assert(original_calls == 4 && published == 1);
    SelectionHistory history;
    for (int i = 0; i < 20; ++i) history.append("{\"eventId\":" + std::to_string(i) + "}");
    assert(history.rows.size() == 16 && history.sequence == 20 && history.dropped == 4);
    assert(history.json().find("\"eventId\":19") != std::string::npos);
    const auto recent = history.json(4);
    assert(recent.find("\"windowOmittedCount\":12") != std::string::npos);
    assert(recent.find("\"eventId\":15") == std::string::npos);
    assert(recent.find("\"eventId\":16") != std::string::npos);
    assert(history.rows.size() == 16 && history.dropped == 4);
    history.append(std::string(SelectionHistory::max_bytes + 1, 'x'));
    assert(history.sequence == 21 && history.dropped == 5 && history.bytes < SelectionHistory::max_bytes);
    history.append(std::string(SelectionHistory::max_bytes, 'x'));
    assert(history.rows.size() == 1 && history.bytes == SelectionHistory::max_bytes);
    std::cout << "selection observation preserves original call and bounded history\n";
}
