#pragma once

#include "managed_runtime.hpp"
#include "battle_commands.hpp"
#include "enemy_ai_command.hpp"
#include "policy_state.hpp"
#include "policy_channel.hpp"
#include "static_data_probe.hpp"

#include <array>
#include <cstring>
#include <filesystem>
#include <functional>
#include <map>
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
                                               const int* seed_override, PolicyChannel& policy) {
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
    auto trials_before = read_trials();
    auto dead_before = dead_flags();
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
            policy.send("{\"type\":\"decision_request\",\"sequence\":" + std::to_string(request) +
                        ",\"state\":" + decision_state + "}");
            const PolicyReply reply = policy.receive(request);
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
        } else {
            try {
                command = make_original_enemy_ai_command(model, static_data, context, generator_for(enemy_generator), active);
            } catch (const std::exception& error) {
                stop_reason = "enemy_ai_command_unavailable";
                stop_detail = error.what();
                break;
            }
        }
        void* skill = model.get<void*>(command, "SkillCommand");
        void* target = skill ? model.get<void*>(skill, "Target") : nullptr;
        const int skill_type_id = skill ? model.get<int>(skill, "SkillTypeId") : 0;
        model.call(processor, "ApplyCommand", {command});
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
                << rng_before[3] << "]]";
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
           << ",\"actors\":" << actors.str() << ",\"trials\":" << trials.str() << ",\"actions\":" << actions.str();
    return report.str();
}
