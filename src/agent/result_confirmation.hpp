#pragma once
#include <cstdint>

// All access is serialized by the caller. A damage getter is not a UI event.
struct ResultConfirmation {
    std::uint64_t generation{};
    std::uint64_t dialog_generation{};
    std::uint64_t opened_tick{};
    std::uintptr_t dialog{};
    bool battle_verified{};
    bool battle_finished{};
    int finished_read_status{-1}; // -1 unavailable, 0 running, 1 finished
    std::uint64_t ui_epoch{};
    bool generation_retired{};

    void begin_battle() {
        ++generation;
        ++ui_epoch;
        generation_retired = false;
        battle_verified = false;
        battle_finished = false;
        finished_read_status = -1;
        close_dialog();
    }
    void observe_running_battle() {
        battle_verified = true;
        battle_finished = false;
        close_dialog();
    }
    void observe_finished_battle() {
        if (battle_verified) battle_finished = true;
    }
    void observe_selection() {
        // Returning to preparation ends the old battle's result lifecycle.
        // A late OnEnabled/getter cannot revive it before a new battle begins.
        if (!generation_retired) ++ui_epoch;
        generation_retired = true;
        battle_verified = false;
        battle_finished = false;
        finished_read_status = -1;
        close_dialog();
    }
    bool open_dialog(std::uintptr_t context, std::uint64_t tick,
                     std::uint64_t callback_epoch) {
        if (!context || !generation || generation_retired || callback_epoch != ui_epoch)
            return false;
        dialog = context;
        dialog_generation = generation;
        opened_tick = tick;
        return true;
    }
    bool open_dialog(std::uintptr_t context, std::uint64_t tick) {
        return open_dialog(context, tick, ui_epoch);
    }
    void close_dialog() {
        dialog = 0;
        dialog_generation = 0;
        opened_tick = 0;
    }
    bool is_open(std::uintptr_t context) const {
        return !generation_retired && context && dialog == context && generation && dialog_generation == generation;
    }
    bool permits_restart(std::uintptr_t context) const {
        return is_open(context) && battle_verified && battle_finished;
    }
};
