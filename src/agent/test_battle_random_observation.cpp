#include "battle_random_observation.hpp"

#include <cassert>
#include <iostream>

int main() {
    using namespace raid::observation;
    RandomFrame frame{1, 2, 3, 100, 40, -1, true, {0xFFFFFFFF, 2, 3, 4}};
    int reads = 0;
    auto stable = observe_random([&](RandomFrame& out) { ++reads; out = frame; return true; });
    assert(stable.available && stable.frame == frame && reads == 2);
    assert(stable.frame.words[0] == 0xFFFFFFFFu);
    assert(!observe_random([](RandomFrame&) { return false; }).available);
    assert(!observe_random([](RandomFrame&) { return true; }).available);
    reads = 0;
    auto moving = observe_random([&](RandomFrame& out) { out = frame; out.words[3] = ++reads; return true; });
    assert(!moving.available && reads == 6);
    reads = 0;
    auto changed_context = observe_random([&](RandomFrame& out) { out = frame; out.context = ++reads; return true; });
    assert(!changed_context.available);
    reads = 0;
    auto settled = observe_random([&](RandomFrame& out) { out = frame; if (++reads == 1) out.turn = 99; return true; });
    assert(settled.available && settled.frame.turn == 100 && reads == 4);
    frame.seed_available = false;
    auto unknown_seed = observe_random([&](RandomFrame& out) { out = frame; return true; });
    assert(unknown_seed.available && !unknown_seed.frame.seed_available);
    frame.setup_id_available = true;
    reads = 0;
    auto changed_setup = observe_random([&](RandomFrame& out) { out = frame; out.setup_id[0] = ++reads; return true; });
    assert(!changed_setup.available && reads == 6);
    std::cout << "read-only random observation tests passed\n";
}
