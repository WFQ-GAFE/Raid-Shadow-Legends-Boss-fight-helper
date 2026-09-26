#include "result_confirmation.hpp"
#include "event_history.hpp"
#include <cstdlib>

void check(bool value) { if (!value) std::abort(); }
int main() {
    ResultConfirmation state;
    state.begin_battle();
    state.observe_running_battle();
    // A getter and even an open dialog cannot terminate a running battle.
    check(!state.permits_restart(100));
    EventHistory history;
    for (int i = 0; i < 500; ++i) history.append("{\"sequence\":" + std::to_string(i) + "}");
    check(history.events.size() == 32);
    check(history.json().find("\"sequence\":499") != std::string::npos);
    check(history.json().find("\"sequence\":0}") == std::string::npos);
    history.append(std::string(9000, 'x'));
    check(history.bytes <= 8192 && history.json() == "[]");
    EventHistory repeats;
    for (int i = 0; i < 500; ++i) repeats.append_state("\"screen\":\"result\"", i);
    check(repeats.sequence == 1 && repeats.events.size() == 1);
    repeats.append_state("\"screen\":\"battle\"", 600);
    check(repeats.sequence == 2 && repeats.events.size() == 2);
    state.open_dialog(100, 20);
    check(!state.permits_restart(100));
    state.observe_finished_battle();
    check(state.permits_restart(100));
    check(!state.permits_restart(200));
    state.close_dialog();
    check(!state.permits_restart(100));
    // A reused dialog address cannot carry confirmation into the next battle.
    state.open_dialog(100, 30);
    state.begin_battle();
    check(!state.permits_restart(100));
    state.open_dialog(100, 40);
    state.observe_finished_battle();
    check(!state.permits_restart(100)); // no verified battle in this generation
    state.observe_running_battle();
    state.observe_finished_battle();
    state.open_dialog(100, 50);
    check(state.permits_restart(100));
    state.observe_running_battle();
    check(!state.permits_restart(100));

    // Logged failure: result -> preparation -> late old result callback.
    state.observe_finished_battle();
    check(state.open_dialog(100, 60));
    const auto old_callback_epoch = state.ui_epoch;
    state.observe_selection();
    check(state.generation_retired && !state.battle_verified);
    check(!state.open_dialog(100, 70, old_callback_epoch));
    check(!state.open_dialog(100, 70)); // even a callback entered after preparation
    check(!state.is_open(100) && !state.permits_restart(100));
    // Repeated preparation capture does not resurrect or change this identity.
    const auto preparation_epoch = state.ui_epoch;
    for (int i = 0; i < 100; ++i) state.observe_selection();
    check(state.ui_epoch == preparation_epoch && state.dialog == 0);

    // Normal results remain usable in the next battle, even at a reused address.
    state.begin_battle();
    state.observe_running_battle();
    state.observe_finished_battle();
    check(!state.open_dialog(100, 80, old_callback_epoch));
    check(state.open_dialog(100, 80));
    check(state.permits_restart(100));

    // OnEnabled reentrancy across a new battle cannot overwrite its own result.
    const auto nested_epoch = state.ui_epoch;
    state.begin_battle();
    state.observe_running_battle();
    state.observe_finished_battle();
    check(state.open_dialog(200, 90));
    check(!state.open_dialog(100, 100, nested_epoch));
    check(state.permits_restart(200) && state.opened_tick == 90);
}
