#pragma once

#include "managed_runtime.hpp"
#include "battle_commands.hpp"
#include "battle_fork.hpp"
#include "enemy_ai_command.hpp"
#include "policy_state.hpp"
#include "policy_channel.hpp"
#include "static_data_probe.hpp"
#include "command_breakdown.hpp"

#include <array>
#include <cstring>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

// One captured Chimera battle in the original engine, player turns chosen by
// the external strategy over the channel (or, when it has no action, by the
// original AI and reported as such), Chimera turns by the original
// EnemyTurnActionGenerator. seed_override replays the same team and Chimera
// with a different battle seed. Returns the report's JSON fields.
inline std::string run_chimera_policy_forecast(const ManagedRuntime& model, void* static_data,
                                               const std::function<void(const char*)>& checkpoint,
                                               const std::filesystem::path& setup_path,
                                               const std::filesystem::path& settings_path,
                                               const int* seed_override, PolicyChannel& policy,
                                               BattleCloner* prepared_cloner = nullptr) {
    void* domain = model.api<void* (*)()>("il2cpp_domain_get")();
    checkpoint("load_captured_battle_settings");
    void* parameters = model.allocate("SharedModel.Meta.Users", "GameParameters", true);
    for (const char* field : {"BattleSettings", "HeroSettings", "DebugSettings", "MasterySettings", "RelicSettings"})
        model.set(parameters, field, model.allocate(model.field_class(parameters, field), true));
    model.set(parameters, "BattleSettings",
              unpack_messagepack(model, domain, settings_path, model.field_class(parameters, "BattleSettings")));
    const int engine_version = model.get<int>(model.get<void*>(parameters, "BattleSettings"), "ActiveEngineVersion");
    model.set_static(model.klass("SharedModel", "SharedModelManager"), "GameParameters", parameters);

    checkpoint("load_captured_battle_setup");
    void* setup = unpack_messagepack(model, domain, setup_path, model.klass("SharedModel.Battle.Core.Setup", "BattleSetup"));
    if (model.get<int>(setup, "KindId") != 8) throw std::runtime_error("Chimera simulation requires a captured Chimera setup");
    const int captured_seed = model.get<int>(setup, "RandomSeed");
    if (seed_override) model.set<int>(setup, "RandomSeed", *seed_override);

    checkpoint("construct_original_battle_processor");
    void* journal = model.get_static<void*>(model.klass("SharedModel.Battle.Journal", "EmptyBattleJournal"), "Instance");
    void* processor = model.allocate("SharedModel.Battle.Core", "BattleProcessor");
    bool first = true;
    model.invoke(model.method(model.object_class(processor), ".ctor", 3), processor, {setup, journal, &first});
    auto team_heroes = [&](void* state, const char* team) {
        void* value = state ? model.get<void*>(state, team) : nullptr;
        return value ? model.get<void*>(value, "Heroes") : nullptr;
    };
    {
        void* state = model.get<void*>(model.call(processor, "get_Context"), "State");
        void* heroes = team_heroes(state, "FirstTeam");
        if (!heroes || model.get<int>(heroes, "_size") == 0) model.call(processor, "InitTeams");
    }

    checkpoint("start_original_battle");
    const auto started_at = GetTickCount64();
    model.call(processor, "StartBattle");
    void* context = model.call(processor, "get_Context");
    void* state = model.get<void*>(context, "State");
    void* random = model.get<void*>(state, "Random");
    void* chimera = model.call(state, "Chimera");
    if (!chimera) throw std::runtime_error("Original Chimera battle has no Chimera hero");
    const auto player_owner = model.get<std::int64_t>(model.get<void*>(setup, "FirstTeam"), "TeamOwnerId");

    struct TrialValue { bool started{}; std::int64_t current{}; int counter{-1}; bool completed{}; };
    auto nullable_int = [&](void* object, const char* field) {
        const auto raw = model.get<std::array<unsigned char, 8>>(object, field);
        int value = -1;
        if (raw[0]) std::memcpy(&value, raw.data() + 4, 4);
        return value;
    };
    checkpoint("prepare_policy_projection");
    OfflinePolicyProjection projection(model, static_data, setup);
    auto read_trials = [&] {
        std::map<int, TrialValue> values;
        for (const auto& [id, slot] : projection.entries(model.get<void*>(chimera, "Challenges"))) {
            void* c = *static_cast<void**>(slot);
            if (!c) continue;
            values[id] = {model.get<bool>(c, "IsStarted"), model.get<std::int64_t>(c, "CurrentProgress"),
                          nullable_int(c, "CurrentCounter"), model.unbox<bool>(model.call(c, "get_IsCompleted"))};
        }
        return values;
    };
    auto dead_flags = [&] {
        std::map<int, bool> dead;
        for (const char* team : {"FirstTeam", "SecondTeam"}) {
            void* heroes = team_heroes(state, team);
            for (int i = 0; heroes && i < model.get<int>(heroes, "_size"); ++i) {
                void* hero = model.call(heroes, "get_Item", {&i});
                void* hero_state = model.get<void*>(hero, "_heroState");
                dead[model.unbox<int>(model.call(hero, "get_Id"))] = hero_state && model.get<bool>(hero_state, "IsDead");
            }
        }
        return dead;
    };

    void* enemy_generator = nullptr;
    void* auto_generator = nullptr;
    std::uint64_t policy_requests = 0, policy_commands = 0, auto_commands = 0;
    std::string stop_reason, stop_detail;
    std::ostringstream actions;
    actions.precision(15);  // Damage values exceed the default six digits.
    actions << '[';
    int commands = 0;
    constexpr int max_skill_commands = 20000;
    PhaseTiming timing;
    // Damage to the Chimera as its damage statistics count it (unknown without statistics).
    CommandBreakdown breakdown(model, [&](void* unit) {
        return unit == chimera && model.get<void*>(state, "ChimeraStatisticsByMultiplier")
            ? projection.chimera_damage(state) : -1.0;
    });
    breakdown.observe(state);
    auto trials_before = read_trials();
    auto dead_before = dead_flags();

    // "What if" for a policy that waits for a hero's last action before the
    // Chimera's next turn: on a copy of the battle the active hero takes the
    // asked action, then the battle plays on (other heroes asked from the
    // policy as lookahead requests) until this hero starts another turn
    // ("again": an extra turn or a full turn meter), the Chimera acts ("boss")
    // or the battle ends ("end"). A free action (a mythical's form change) keeps
    // the same turn: the hero is asked again and the copy plays on. The original
    // battle is never touched; its CalcHash is checked after every copy.
    // A prestarted worker has already shared the static data (prepared_cloner).
    std::unique_ptr<BattleCloner> own_cloner;
    BattleCloner* cloner = prepared_cloner;
    bool battle_shared = false;
    std::uint64_t lookahead_requests = 0;
    struct {
        int probes{}, nested{}, actions{};
        int validated{}, mismatches{}, statistics_changed{}, changed_by_full{};  // validation mode only
        double shared_ms{}, clone_ms{}, full_clone_ms{}, total_ms{};
        std::size_t shared_objects{}, copied_objects{}, shared_lists{};
        std::vector<std::string> largest;  // classes of the largest copy
    } look;
    // RSL_FORK_VALIDATE=1 (tests): every move is also played on a full copy and compared.
    const bool validate_fork = [] {
        char value[4]{};
        return GetEnvironmentVariableA("RSL_FORK_VALIDATE", value, sizeof(value)) == 1 && value[0] == '1';
    }();
    // The statistics of finished turns: BattleStatistics.StatisticsByHero -> HeroStatistics
    // ._statsPerRound -> HeroBattleStatistics._statisticsByTurn / _skillInfoUsedByTurn,
    // each a Dictionary<turn, List<...>>; the lists of turns before `turn`.
    auto finished_turn_lists = [&](void* statistics, int turn) {
        std::vector<void*> lists;
        if (!statistics) return lists;
        for (const auto& [hero_id, hero_slot] : projection.entries(model.get<void*>(statistics, "StatisticsByHero"))) {
            void* hero = *static_cast<void**>(hero_slot);
            if (!hero) continue;
            for (const auto& [round, round_slot] : projection.entries(model.get<void*>(hero, "_statsPerRound"))) {
                void* by_round = *static_cast<void**>(round_slot);
                if (!by_round) continue;
                for (const char* field : {"_statisticsByTurn", "_skillInfoUsedByTurn"})
                    for (const auto& [key, list_slot] : projection.entries(model.get<void*>(by_round, field)))
                        if (key < turn && *static_cast<void**>(list_slot)) lists.push_back(*static_cast<void**>(list_slot));
            }
        }
        return lists;
    };
    LARGE_INTEGER frequency{};
    QueryPerformanceFrequency(&frequency);
    auto ms_since = [&](std::uint64_t start) {
        return static_cast<double>(PhaseTiming::now() - start) * 1000.0 / static_cast<double>(frequency.QuadPart);
    };
    auto lookahead = [&](const PolicyReply& asked, std::uint64_t request) -> std::string {
        const auto started = PhaseTiming::now();
        ++look.probes;
        if (!cloner) {
            own_cloner = std::make_unique<BattleCloner>(model);
            cloner = own_cloner.get();
            cloner->share({static_data});
        }
        if (!battle_shared) {
            look.shared_objects = cloner->share({setup, model.get<void*>(context, "Setup"),
                                                 model.get_static<void*>(model.klass("SharedModel", "SharedModelManager"),
                                                                         "GameParameters"),
                                                 model.get<void*>(processor, "Journal")});
            battle_shared = true;
            look.shared_ms = ms_since(started);
        }
        const int hash_before = model.unbox<int>(model.call(state, "CalcHash"));
        // The battle statistics keep a record of every effect, grouped by turn (two
        // thirds of a late copy). A finished turn's lists are never written again, so
        // the copy shares them; the dictionaries holding them are still copied. The
        // validation mode also plays the move on a full copy first and checks the
        // main statistics are unchanged after the lean one.
        void* statistics = model.get<void*>(context, "Statistics");
        const std::uint64_t statistics_before = validate_fork && statistics ? cloner->digest(statistics) : 0;
        const int current_turn = model.get<int>(state, "CurrentTurn");
        auto branch = [&](bool lean, std::string& detail, int& actions_played) -> std::string {
            const auto copy_started = PhaseTiming::now();
            cloner->borrow_next(lean ? finished_turn_lists(statistics, current_turn) : std::vector<void*>{});
            void* copy = cloner->clone(processor);
            (lean ? look.clone_ms : look.full_clone_ms) += ms_since(copy_started);
            if (lean) look.shared_lists += cloner->borrowed();
            if (lean && cloner->copied() > look.copied_objects) {
                look.copied_objects = cloner->copied();
                // Validation only (it costs time): the originals are alive only now.
                if (validate_fork) look.largest = cloner->largest_classes(6);
            }
            void* copy_context = model.call(copy, "get_Context");
            void* copy_state = model.get<void*>(copy_context, "State");
            std::string result = "end";
            try {
                void* asker = model.get<void*>(copy_state, "ActiveHero");
                const int asked_turns = asker ? model.get<int>(asker, "TurnCount") : -1;
                model.call(copy, "ApplyCommand", {make_original_player_command_from_input(
                    model, static_data, copy_state, player_owner, asked.actor_id, asked.skill_type_id,
                    asked.target_id, true, "Lookahead command")});
                void* copy_generator = nullptr;
                while (!model.get<bool>(copy_state, "BattleFinished")) {
                    void* next = model.get<void*>(copy_state, "ActiveHero");
                    if (!next) {
                        detail = "no_active_hero";
                        return "unknown";
                    }
                    if (model.unbox<std::int64_t>(model.call(next, "get_OwnerUserId")) != player_owner) return "boss";
                    // The same hero in the same turn (a mythical's free form change) plays on.
                    if (model.unbox<int>(model.call(next, "get_Id")) == asked.actor_id &&
                        model.get<int>(next, "TurnCount") != asked_turns)
                        return "again";
                    if (++actions_played > 40) {
                        detail = "too_many_actions";
                        return "unknown";
                    }
                    const std::uint64_t nested = 1000000000ULL + ++lookahead_requests;
                    ++look.nested;
                    policy.send("{\"type\":\"decision_request\",\"sequence\":" + std::to_string(nested) +
                                ",\"lookahead\":true,\"state\":" + projection.decision_state(copy_state, nested) + "}");
                    const PolicyReply answer = policy.receive(nested);
                    void* command = nullptr;
                    if (answer.command) {
                        command = make_original_player_command_from_input(
                            model, static_data, copy_state, player_owner, answer.actor_id, answer.skill_type_id,
                            answer.target_id, true, "Lookahead policy command");
                    } else if (answer.automatic) {
                        if (!copy_generator) {
                            void* generator_class = model.klass("SharedModel.Battle.AI.TurnActionGeneration",
                                                                "EnemyTurnActionGenerator");
                            copy_generator = model.allocate(generator_class);
                            model.invoke(model.method(generator_class, ".ctor", 1), copy_generator, {copy_context});
                        }
                        command = make_original_enemy_ai_command(model, static_data, copy_context, copy_generator, next);
                    } else {
                        detail = answer.probe ? "nested_probe" : "policy_stopped";
                        return "unknown";
                    }
                    model.call(copy, "ApplyCommand", {command});
                }
            } catch (const std::exception& error) {
                detail = std::string(error.what()).substr(0, 160);
                for (char& c : detail)
                    if (c == '"' || c == '\\' || static_cast<unsigned char>(c) < 32) c = '_';
                return "unknown";
            }
            return result;
        };
        std::string detail;
        int actions_played = 0;
        std::string result;
        if (validate_fork) {
            // The full copy first: whatever changes the main statistics after it is the lean copy's doing.
            std::string full_detail;
            int full_actions = 0;
            const std::string full = branch(false, full_detail, full_actions);
            const bool changed_by_full = statistics && cloner->digest(statistics) != statistics_before;
            result = branch(true, detail, actions_played);
            const bool changed_after_lean = statistics && cloner->digest(statistics) != statistics_before;
            ++look.validated;
            if (full != result || full_actions != actions_played) ++look.mismatches;
            if (changed_by_full) ++look.changed_by_full;
            else if (changed_after_lean) ++look.statistics_changed;
        } else {
            result = branch(true, detail, actions_played);
        }
        if (model.unbox<int>(model.call(state, "CalcHash")) != hash_before)
            throw std::runtime_error("A battle copy changed the original battle");
        look.actions += actions_played;
        look.total_ms += ms_since(started);
        return "{\"type\":\"probe_result\",\"sequence\":" + std::to_string(request) + ",\"result\":\"" + result +
               "\",\"actions\":" + std::to_string(actions_played) +
               (detail.empty() ? std::string() : ",\"detail\":\"" + detail + "\"") + "}";
    };
    while (!model.get<bool>(state, "BattleFinished") && commands < max_skill_commands) {
        std::size_t root_frame = model.root_mark();
        if (commands % 50 == 0) {
            const std::string progress = "execute_turn_" + std::to_string(model.get<int>(state, "CurrentTurn")) +
                                         "_boss_turn_" + std::to_string(model.get<int>(chimera, "TurnCount"));
            checkpoint(progress.c_str());
        }
        void* active = model.get<void*>(state, "ActiveHero");
        if (!active) throw std::runtime_error("Original battle has no active hero before a skill command");
        const int active_id = model.unbox<int>(model.call(active, "get_Id"));
        const bool player_action = model.unbox<std::int64_t>(model.call(active, "get_OwnerUserId")) == player_owner;
        const int turn_before = model.get<int>(state, "CurrentTurn");
        const int boss_turns_before = model.get<int>(chimera, "TurnCount");
        const int form_before = model.get<int>(chimera, "CurrentFormIndex");
        // Per-action damage from the game's own Chimera damage statistics (the
        // number the objectives use); Boss DamageTaken only when a setup
        // keeps no statistics (it saturates at the Q32.32 maximum).
        const double statistics_before = projection.chimera_damage(state);
        const double taken_before = static_cast<double>(model.get<std::int64_t>(chimera, "DamageTaken")) / 4294967296.0;
        const auto rng_before = offline_rng_words(model, random);
        void* command = nullptr;
        const char* source = "enemy";
        std::string reason;
        auto generator_for = [&](void*& generator) -> void* {
            if (!generator) {
                void* generator_class = model.klass("SharedModel.Battle.AI.TurnActionGeneration", "EnemyTurnActionGenerator");
                generator = model.allocate(generator_class);
                model.invoke(model.method(generator_class, ".ctor", 1), generator, {context});
                root_frame = model.root_mark();  // Generators live for the whole battle.
            }
            return generator;
        };
        timing.lap(PhaseTiming::kOther);
        if (player_action) {
            const std::uint64_t request = ++policy_requests;
            std::string decision_state;
            try {
                decision_state = projection.decision_state(state, request);
            } catch (const std::exception& error) {
                stop_reason = "decision_state_unavailable";
                stop_detail = error.what();
                break;
            }
            timing.lap(PhaseTiming::kDecisionState);
            policy.send("{\"type\":\"decision_request\",\"sequence\":" + std::to_string(request) +
                        ",\"state\":" + decision_state + "}");
            PolicyReply reply = policy.receive(request);
            while (reply.probe) {
                timing.lap(PhaseTiming::kPolicyWait);
                const std::string answer = lookahead(reply, request);
                timing.lap(PhaseTiming::kLookahead);
                policy.send(answer);
                reply = policy.receive(request);
            }
            timing.lap(PhaseTiming::kPolicyWait);
            if (reply.automatic) {
                command = make_original_enemy_ai_command(model, static_data, context, generator_for(auto_generator), active);
                source = "auto";
                reason = reply.reason;
                ++auto_commands;
            } else if (!reply.command) {
                stop_reason = "policy_stopped";
                stop_detail = reply.reason;
                break;
            } else {
                try {
                    command = make_original_player_command_from_input(
                        model, static_data, state, player_owner, reply.actor_id, reply.skill_type_id,
                        reply.target_id, true, "Policy command " + std::to_string(request));
                } catch (const std::exception& error) {
                    stop_reason = "policy_command_rejected";
                    stop_detail = error.what();
                    break;
                }
                source = "policy";
                ++policy_commands;
            }
            timing.lap(PhaseTiming::kPlayerCommand);
        } else {
            try {
                command = make_original_enemy_ai_command(model, static_data, context, generator_for(enemy_generator), active);
            } catch (const std::exception& error) {
                stop_reason = "enemy_ai_command_unavailable";
                stop_detail = error.what();
                break;
            }
        }
        timing.lap(PhaseTiming::kEnemyAi);
        void* skill = model.get<void*>(command, "SkillCommand");
        void* target = skill ? model.get<void*>(skill, "Target") : nullptr;
        const int skill_type_id = skill ? model.get<int>(skill, "SkillTypeId") : 0;
        timing.lap(PhaseTiming::kOther);
        void* processed = model.call(processor, "ApplyCommand", {command});
        timing.lap(PhaseTiming::kApply);
        const auto trials_after = read_trials();
        const auto dead_after = dead_flags();
        if (commands) actions << ',';
        // Compact row: see chimera_simulation.ACTION_FIELDS on the Python side.
        actions << '[' << turn_before << ',' << boss_turns_before << ',' << form_before << ',' << active_id << ','
                << model.get<int>(active, "TypeId") << ",\"" << source << "\"," << skill_type_id << ','
                << (target ? model.get<int>(target, "HeroId") : -1) << ','
                << [&] {
                       const double statistics_after = projection.chimera_damage(state);
                       if (statistics_after != 0.0 || statistics_before != 0.0) return statistics_after - statistics_before;
                       return static_cast<double>(model.get<std::int64_t>(chimera, "DamageTaken")) / 4294967296.0 - taken_before;
                   }()
                << ",[";
        bool first_trial = true;
        for (const auto& [id, after] : trials_after) {
            const auto found = trials_before.find(id);
            const TrialValue before = found == trials_before.end() ? TrialValue{} : found->second;
            if (before.started == after.started && before.current == after.current &&
                before.counter == after.counter && before.completed == after.completed)
                continue;
            if (!first_trial) actions << ',';
            first_trial = false;
            actions << '[' << id << ',' << before.current << ',' << after.current << ',' << before.counter << ','
                    << after.counter << ',' << (after.started ? 1 : 0) << ',' << (after.completed ? 1 : 0) << ']';
        }
        actions << "],[";
        bool first_death = true;
        for (const auto& [id, dead] : dead_after) {
            const auto found = dead_before.find(id);
            if (dead == (found != dead_before.end() && found->second)) continue;
            if (!first_death) actions << ',';
            first_death = false;
            actions << (dead ? id : -1 - id);  // negative: revived
        }
        actions << "],\"" << reason << "\",[" << rng_before[0] << ',' << rng_before[1] << ',' << rng_before[2] << ','
                << rng_before[3] << "],";
        breakdown.append(actions, processed, state);
        actions << ']';
        trials_before = trials_after;
        dead_before = dead_after;
        ++commands;
        model.release_roots_since(root_frame);
    }
    actions << ']';
    const bool finished = model.get<bool>(state, "BattleFinished");
    if (stop_reason.empty()) stop_reason = finished ? "battle_finished" : "skill_command_limit";
    for (char& c : stop_detail)
        if (c == '"' || c == '\\' || static_cast<unsigned char>(c) < 32) c = '_';

    checkpoint("collect_original_battle_result");
    void* result = finished ? model.call(processor, "GetBattleResult") : nullptr;
    std::ostringstream trials;
    trials << '[';
    bool first_trial = true;
    for (const auto& [id, slot] : projection.entries(model.get<void*>(chimera, "Challenges"))) {
        void* c = *static_cast<void**>(slot);
        if (!c) continue;
        if (!first_trial) trials << ',';
        first_trial = false;
        trials << "{\"id\":" << id << ",\"started\":" << (model.get<bool>(c, "IsStarted") ? "true" : "false")
               << ",\"completed\":" << (model.unbox<bool>(model.call(c, "get_IsCompleted")) ? "true" : "false")
               << ",\"currentRaw\":" << model.get<std::int64_t>(c, "CurrentProgress")
               << ",\"targetRaw\":" << model.get<std::int64_t>(c, "TargetProgress")
               << ",\"counterLimit\":" << nullable_int(c, "CounterLimit")
               << ",\"currentCounter\":" << nullable_int(c, "CurrentCounter")
               << ",\"selfTurnWhichCompleted\":" << nullable_int(c, "SelfTurnWhichCompleted") << '}';
    }
    trials << ']';
    std::ostringstream actors;
    actors << '[';
    bool first_actor = true;
    for (const char* team : {"FirstTeam", "SecondTeam"}) {
        void* heroes = team_heroes(state, team);
        for (int i = 0; heroes && i < model.get<int>(heroes, "_size"); ++i) {
            void* hero = model.call(heroes, "get_Item", {&i});
            void* hero_state = model.get<void*>(hero, "_heroState");
            if (!first_actor) actors << ',';
            first_actor = false;
            const std::int64_t health = model.get<std::int64_t>(hero, "Health");
            const std::int64_t maximum = model.unbox<std::int64_t>(model.call(hero, "get_MaxHealth"));
            actors << "{\"actorId\":" << model.unbox<int>(model.call(hero, "get_Id"))
                   << ",\"heroTypeId\":" << model.get<int>(hero, "TypeId")
                   << ",\"inventoryHeroId\":" << model.get<int>(hero, "InventoryHeroId")
                   << ",\"slotId\":" << model.get<int>(hero, "SlotId")
                   << ",\"player\":" << (std::string(team) == "FirstTeam" ? "true" : "false")
                   << ",\"dead\":" << (hero_state && model.get<bool>(hero_state, "IsDead") ? "true" : "false")
                   << ",\"healthPct\":" << (maximum > 0 ? static_cast<double>(health) * 100.0 / static_cast<double>(maximum) : 0.0)
                   << '}';
        }
    }
    actors << ']';
    std::ostringstream report;
    report.precision(15);
    report << "\"policyDriven\":true,\"engineVersion\":" << engine_version
           << ",\"capturedSeed\":" << captured_seed << ",\"seed\":" << model.get<int>(setup, "RandomSeed")
           << ",\"seedOverridden\":" << (seed_override ? "true" : "false")
           << ",\"battleSetupId\":\"" << projection.setup_id() << '"'
           << ",\"battleKindId\":8,\"stageId\":" << model.get<int>(setup, "StageId")
           << ",\"bossTypeId\":" << model.get<int>(chimera, "TypeId")
           << ",\"turn\":" << model.get<int>(state, "CurrentTurn")
           << ",\"bossTurns\":" << model.get<int>(chimera, "TurnCount")
           << ",\"stopReason\":\"" << stop_reason << '"'
           << ",\"policyStop\":" << (stop_detail.empty() ? std::string("null") : "\"" + stop_detail + "\"")
           << ",\"elapsedMs\":" << (GetTickCount64() - started_at)
           << ",\"battleFinished\":" << (finished ? "true" : "false")
           << ",\"resultType\":" << (result ? std::to_string(model.get<int>(result, "ResultType")) : "null")
           << ",\"finishCause\":" << (result ? std::to_string(model.get<int>(result, "FinishCause")) : "null")
           << ",\"damage\":" << projection.chimera_damage(state)
           << ",\"bossDamageTaken\":" << static_cast<double>(model.get<std::int64_t>(chimera, "DamageTaken")) / 4294967296.0
           << ",\"commands\":" << commands << ",\"policyRequests\":" << policy_requests
           << ",\"policyCommands\":" << policy_commands << ",\"autoCommands\":" << auto_commands
           << ",\"phaseMs\":" << timing.json()
           << ",\"lookahead\":{\"probes\":" << look.probes << ",\"nestedDecisions\":" << look.nested
           << ",\"actions\":" << look.actions << ",\"sharedObjects\":" << look.shared_objects
           << ",\"copiedObjects\":" << look.copied_objects << ",\"sharedMs\":" << look.shared_ms
           << ",\"cloneMs\":" << look.clone_ms << ",\"totalMs\":" << look.total_ms
           << ",\"sharedLists\":" << look.shared_lists;
    if (validate_fork)
        report << ",\"validated\":" << look.validated << ",\"mismatches\":" << look.mismatches
               << ",\"statisticsChanged\":" << look.statistics_changed << ",\"changedByFull\":" << look.changed_by_full
               << ",\"fullCloneMs\":" << look.full_clone_ms;
    report << ",\"largestClasses\":[";
    {
        bool first_class = true;
        for (auto& entry : look.largest) {
            for (char& c : entry)
                if (c == '"' || c == '\\' || static_cast<unsigned char>(c) < 32) c = '_';
            report << (first_class ? "" : ",") << '"' << entry << '"';
            first_class = false;
        }
    }
    report << "]}"
           << ",\"actors\":" << actors.str() << ",\"trials\":" << trials.str() << ",\"actions\":" << actions.str();
    return report.str();
}
