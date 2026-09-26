#pragma once

// Included inside agent.cpp's namespace after the ordinary field readers.
// Compiled only for the explicitly selected research capture build.
// This observes the selector's actual argument list. It neither constructs a
// replacement game list nor invokes a game getter, selector or RNG method.

ObjectListItems complete_selector_list(void* list, bool& complete) {
    std::int32_t size = -1;
    if (!list || !safe_read_field(list, "_size", "size", size) || size < 0 || size > 128) {
        complete = false;
        return {};
    }
    if (size == 0) { ObjectListItems empty{}; empty.valid = true; return empty; }
    auto values = read_object_list(list);
    complete = complete && values.valid && values.count == static_cast<std::size_t>(size);
    return values;
}

ObjectListItems selector_dictionary_values(void* dictionary, bool& complete) {
    if (!dictionary) { ObjectListItems empty{}; empty.valid = true; return empty; }
    std::int32_t count = -1;
    if (!safe_read_field(dictionary, "_count", "count", count) || count < 0) {
        complete = false;
        return {};
    }
    if (count == 0) { ObjectListItems empty{}; empty.valid = true; return empty; }
    auto values = read_object_dictionary_values(dictionary);
    // Existing generic readers are bounded. Partial coverage can prove a
    // matching record exists, but cannot prove that no record exists.
    // The generic reader skips null, deleted and unreadable entries. Unless
    // every used entry was returned, conservatively reject an absence proof.
    complete = complete && values.valid && count <= 128 &&
        values.count == static_cast<std::size_t>(count);
    return values;
}

ObjectListItems selector_all_heroes(void* state, bool& complete) {
    ObjectListItems heroes{};
    void* cached = nullptr;
    if (!safe_read_field(state, "_allHeroesCache", nullptr, cached)) { complete = false; return {}; }
    if (cached) {
        std::size_t count = 0;
        if (!safe_read(cached, 24, count) || count > heroes.objects.size()) { complete = false; return {}; }
        for (std::size_t i = 0; i < count; ++i) {
            void* hero = nullptr;
            if (!safe_read(cached, 32 + sizeof(void*) * i, hero) || !hero) { complete = false; return {}; }
            heroes.objects[heroes.count++] = hero;
        }
        heroes.valid = true;
        return heroes;
    }
    for (const char* name : {"FirstTeam", "SecondTeam"}) {
        void* team = nullptr;
        void* list = nullptr;
        if (!safe_read_field(state, name, nullptr, team) || !team ||
            !safe_read_field(team, "Heroes", nullptr, list)) { complete = false; return {}; }
        const auto values = complete_selector_list(list, complete);
        for (std::size_t i = 0; i < values.count; ++i) {
            if (heroes.count == heroes.objects.size()) { complete = false; return heroes; }
            heroes.objects[heroes.count++] = values.objects[i];
        }
    }
    heroes.valid = complete;
    return heroes;
}

struct FirstApplicationObservation {
    bool known{};
    bool first{};
    std::size_t scanned{};
    const char* reason{"history_unavailable"};
};

FirstApplicationObservation observe_first_mark_application(void* context, void* effect_context, std::int32_t effect_id) {
    FirstApplicationObservation result{};
    const auto deadline = GetTickCount64() + 8;
    void* state = nullptr;
    void* statistics = nullptr;
    void* by_hero = nullptr;
    bool complete = true;
    if (!safe_read_field(context, "State", nullptr, state) || !state ||
        !safe_read_field(context, "Statistics", nullptr, statistics) || !statistics ||
        !safe_read_field(statistics, "StatisticsByHero", nullptr, by_hero) || !by_hero) return result;
    const auto heroes = selector_all_heroes(state, complete);
    complete = complete && heroes.valid && heroes.count > 0;
    auto statistics_items = selector_dictionary_values(by_hero, complete);
    // Searching the producer first changes only our read order, not game state.
    void* producer = nullptr;
    std::int32_t producer_id = -1;
    if (safe_read_field(effect_context, "Producer", nullptr, producer) && producer)
        safe_read_field(producer, "<Id>k__BackingField", "Id", producer_id);
    std::vector<std::int32_t> hero_ids;
    for (std::size_t i = 0; i < heroes.count; ++i) {
        std::int32_t id = -1;
        if (!safe_read_field(heroes.objects[i], "<Id>k__BackingField", "Id", id) || id < 0) complete = false;
        else hero_ids.push_back(id);
    }
    if (auto found = std::find(hero_ids.begin(), hero_ids.end(), producer_id); found != hero_ids.end())
        std::iter_swap(hero_ids.begin(), found);
    for (const auto id : hero_ids) {
        if (GetTickCount64() > deadline) { result.reason = "history_scan_limit"; return result; }
        void* hero_stats = nullptr;
        for (std::size_t s = 0; s < statistics_items.count; ++s) {
            std::int32_t stats_id = -1;
            if (!safe_read_field(statistics_items.objects[s], "Id", nullptr, stats_id)) complete = false;
            if (stats_id == id) { hero_stats = statistics_items.objects[s]; break; }
        }
        if (!hero_stats) continue; // GetWholeHeroStatistics treats missing hero stats as empty.
        void* round_dictionary = nullptr;
        if (!safe_read_field(hero_stats, "_statsPerRound", nullptr, round_dictionary)) { complete = false; continue; }
        const auto rounds = selector_dictionary_values(round_dictionary, complete);
        for (std::size_t r = 0; r < rounds.count; ++r) {
            if (GetTickCount64() > deadline) { result.reason = "history_scan_limit"; return result; }
            void* turn_dictionary = nullptr;
            if (!safe_read_field(rounds.objects[r], "_statisticsByTurn", nullptr, turn_dictionary)) { complete = false; continue; }
            const auto turns = selector_dictionary_values(turn_dictionary, complete);
            for (std::size_t t = 0; t < turns.count; ++t) {
                if (GetTickCount64() > deadline) { result.reason = "history_scan_limit"; return result; }
                const auto records = complete_selector_list(turns.objects[t], complete);
                for (std::size_t e = 0; e < records.count; ++e) {
                    if (++result.scanned > 8192 || GetTickCount64() > deadline) {
                        result.reason = "history_scan_limit";
                        return result;
                    }
                    bool processed = false;
                    std::int32_t recorded_effect = -1;
                    if (!safe_read_field(records.objects[e], "IsEffectProcessed", nullptr, processed)) { complete = false; continue; }
                    if (!processed) continue;
                    if (!safe_read_field(records.objects[e], "EffectId", nullptr, recorded_effect)) { complete = false; continue; }
                    if (recorded_effect == effect_id) {
                        result.known = true;
                        result.first = false;
                        result.reason = "matching_processed_effect";
                        return result;
                    }
                }
            }
        }
    }
    result.known = complete;
    result.first = complete;
    result.reason = complete ? "complete_history_without_match" : "history_incomplete";
    return result;
}

struct HydraSelectorBefore {
    bool enabled{};
    void* context{};
    std::uint64_t event_id{};
    std::string json_prefix;
    raid::observation::RandomObservation random;
};

HydraSelectorBefore capture_hydra_selector_before(void* candidate_list, void* effect_context) {
    HydraSelectorBefore before{};
    if (g_takeover_boss_mode.load(std::memory_order_acquire) != kTakeoverBossModeHydra ||
        !safe_read_field(effect_context, "BattleContext", nullptr, before.context) || !before.context) return before;
    before.enabled = true;
    before.event_id = g_hydra_mark_event_sequence.fetch_add(1, std::memory_order_acq_rel) + 1;
    before.random = raid::observation::observe_random([&](auto& frame) { return read_random_frame(before.context, frame); });
    bool candidates_complete = true;
    const auto candidates = complete_selector_list(candidate_list, candidates_complete);
    void* effect = nullptr;
    std::int32_t effect_id = -1;
    if (!safe_read_field(effect_context, "Effect", nullptr, effect) || !effect ||
        !safe_read_field(effect, "Id", nullptr, effect_id)) candidates_complete = false;
    const auto history = effect_id >= 0 ? observe_first_mark_application(before.context, effect_context, effect_id)
                                       : FirstApplicationObservation{};
    std::ostringstream output;
    output << "{\"type\":\"hydra_mark_selection\",\"schema\":1,\"eventId\":" << before.event_id
           << ",\"observedAtTick\":" << GetTickCount64() << ",\"pid\":" << GetCurrentProcessId()
           << ",\"agentInstanceId\":" << (g_shared_state ? g_shared_state->instance_id : 0)
           << ",\"takeoverSession\":" << g_takeover_session.load()
           << ",\"modelContext\":\"" << reinterpret_cast<std::uintptr_t>(before.context) << '"'
           << ",\"battleGeneration\":" << result_confirmation_snapshot().generation
           << ",\"effectId\":" << effect_id
           << ",\"candidateSource\":\"RandomHungerVictimSelectedFrom_argument\",\"candidates\":[";
    for (std::size_t i = 0; i < candidates.count && i < 16; ++i) {
        if (i) output << ',';
        std::int32_t id = -1, type = -1;
        std::int64_t cd = 0;
        void* stats = nullptr;
        const bool valid = safe_read_field(candidates.objects[i], "<Id>k__BackingField", "Id", id) && id >= 0 &&
            safe_read_field(candidates.objects[i], "TypeId", nullptr, type) && type > 0 &&
            safe_read_field(candidates.objects[i], "<Stats>k__BackingField", "Stats", stats) && stats &&
            safe_read_field(stats, "CriticalDamage", nullptr, cd);
        candidates_complete = candidates_complete && valid;
        output << "{\"actorId\":" << id << ",\"heroTypeId\":" << type << ",\"criticalDamageRaw\":";
        if (valid) output << '"' << cd << '"'; else output << "null";
        output << '}';
    }
    candidates_complete = candidates_complete && candidates.count >= 2 && candidates.count <= 16;
    output << "],\"candidatesComplete\":" << (candidates_complete ? "true" : "false")
           << ",\"firstApplication\":";
    if (history.known) output << (history.first ? "true" : "false"); else output << "null";
    output << ",\"historyReadStatus\":\"" << history.reason << "\",\"historyRowsScanned\":" << history.scanned
           << ",\"rngBefore\":";
    append_random_observation(output, before.random, "selector_entry");
    before.json_prefix = output.str();
    return before;
}

void publish_hydra_selector_after(const HydraSelectorBefore& before, void* selected) {
    if (!before.enabled) return;
    const auto after = raid::observation::observe_random([&](auto& frame) { return read_random_frame(before.context, frame); });
    std::int32_t id = -1, type = -1;
    const bool result_read = selected && safe_read_field(selected, "<Id>k__BackingField", "Id", id) && id >= 0 &&
        safe_read_field(selected, "TypeId", nullptr, type) && type > 0;
    std::ostringstream output;
    output << before.json_prefix << ",\"rngAfter\":";
    append_random_observation(output, after, "selector_return");
    output << ",\"selectedActorId\":";
    if (result_read) output << id; else output << "null";
    output << ",\"selectedHeroTypeId\":";
    if (result_read) output << type; else output << "null";
    output << ",\"resultRead\":" << (result_read ? "true" : "false") << '}';
    auto row = output.str();
    AcquireSRWLockExclusive(&g_hydra_mark_history_lock);
    try { g_hydra_mark_history.append(row); }
    catch (...) { ReleaseSRWLockExclusive(&g_hydra_mark_history_lock); throw; }
    ReleaseSRWLockExclusive(&g_hydra_mark_history_lock);
    diagnostic(row);
}

void* __fastcall hook_hydra_mark_selector(void* candidates, void* effect_context, const MethodInfo* method) {
    return raid::observation::observe_selection_call(
        [&]() { return capture_hydra_selector_before(candidates, effect_context); },
        [&]() { return g_original_hydra_mark_selector(candidates, effect_context, method); },
        [&](const auto& before, void* selected) { publish_hydra_selector_after(before, selected); });
}

void append_hydra_selector_history(std::ostringstream& output, std::size_t limit = 16) {
    std::string history;
    AcquireSRWLockShared(&g_hydra_mark_history_lock);
    try { history = g_hydra_mark_history.json(limit); }
    catch (...) { ReleaseSRWLockShared(&g_hydra_mark_history_lock); throw; }
    ReleaseSRWLockShared(&g_hydra_mark_history_lock);
    output << ",\"hydraMarkSelectionHistory\":" << history;
}
