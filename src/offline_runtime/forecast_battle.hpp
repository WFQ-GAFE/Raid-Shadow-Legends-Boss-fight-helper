#pragma once

#include "managed_runtime.hpp"
#include "battle_commands.hpp"
#include "enemy_ai_command.hpp"
#include "policy_state.hpp"
#include "policy_channel.hpp"
#include "static_data_probe.hpp"
#include "command_breakdown.hpp"

#include <array>
#include <filesystem>
#include <functional>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

// One captured Hydra battle in the original engine. Every player command comes
// from the external list policy over the channel; every enemy command from the
// original EnemyTurnActionGenerator. seed_override replays the same team and
// Hydra with a different battle seed. Returns the report's JSON fields.
inline std::string run_policy_forecast(const ManagedRuntime& model, void* static_data,
                                       const std::function<void(const char*)>& checkpoint,
                                       const std::filesystem::path& setup_path,
                                       const std::filesystem::path& settings_path,
                                       int max_game_turn, PolicyChannel& policy,
                                       const int* seed_override = nullptr) {
    if (max_game_turn < 1 || max_game_turn > 1000)
        throw std::runtime_error("Game turn limit must be between 1 and 1000");
    void* domain = model.api<void* (*)()>("il2cpp_domain_get")();
    checkpoint("load_captured_battle_settings");
    void* parameters = model.allocate("SharedModel.Meta.Users", "GameParameters", true);
    for (const char* field : {"BattleSettings", "HeroSettings", "DebugSettings", "MasterySettings", "RelicSettings"})
        model.set(parameters, field, model.allocate(model.field_class(parameters, field), true));
    model.set(parameters, "BattleSettings",
              unpack_messagepack(model, domain, settings_path, model.field_class(parameters, "BattleSettings")));
    const int active_engine_version =
        model.get<int>(model.get<void*>(parameters, "BattleSettings"), "ActiveEngineVersion");
    model.set_static(model.klass("SharedModel", "SharedModelManager"), "GameParameters", parameters);
    const int configured_max_turns = model.get<int>(model.get<void*>(parameters, "BattleSettings"), "MaxTurnsInBattle");

    checkpoint("load_captured_battle_setup");
    void* setup = unpack_messagepack(model, domain, setup_path,
                                     model.klass("SharedModel.Battle.Core.Setup", "BattleSetup"));
    if (model.get<int>(setup, "KindId") != 5)
        throw std::runtime_error("Policy forecast requires a captured Hydra setup");
    const int captured_seed = model.get<int>(setup, "RandomSeed");
    if (seed_override) model.set<int>(setup, "RandomSeed", *seed_override);
    const int effective_max_turns = model.unbox<int>(model.call(setup, "get_MaxTurnsInBattle"));

    checkpoint("construct_original_battle_processor");
    void* journal = model.get_static<void*>(model.klass("SharedModel.Battle.Journal", "EmptyBattleJournal"), "Instance");
    void* processor = model.allocate("SharedModel.Battle.Core", "BattleProcessor");
    bool first = true;
    model.invoke(model.method(model.object_class(processor), ".ctor", 3), processor, {setup, journal, &first});
    auto player_count = [&] {
        void* context = model.call(processor, "get_Context");
        void* state = context ? model.get<void*>(context, "State") : nullptr;
        void* team = state ? model.get<void*>(state, "FirstTeam") : nullptr;
        void* heroes = team ? model.get<void*>(team, "Heroes") : nullptr;
        return heroes ? model.get<int>(heroes, "_size") : 0;
    };
    // The constructor normally initializes the teams already. Calling
    // InitTeams again recreates the heroes and shifts every actor ID.
    if (player_count() == 0) model.call(processor, "InitTeams");
    if (player_count() == 0)
        throw std::runtime_error("Original battle processor has no player heroes after team initialization");

    checkpoint("start_original_battle");
    const auto battle_started_at = GetTickCount64();
    void* start_results = model.call(processor, "StartBattle");
    void* context = model.call(processor, "get_Context");
    void* state = model.get<void*>(context, "State");
    void* random = model.get<void*>(state, "Random");

    // HungerCounter (effect kind 9020) applications, from two independent
    // sources: the post-command state scan and the processor result stream.
    std::set<std::string> seen_hunger;
    std::ostringstream hunger_events;
    hunger_events << '[';
    bool first_hunger = true;
    auto record_hunger = [&] {
        void* heroes = model.get<void*>(model.get<void*>(state, "FirstTeam"), "Heroes");
        for (int i = 0; heroes && i < model.get<int>(heroes, "_size"); ++i) {
            void* hero = model.call(heroes, "get_Item", {&i});
            void* hero_state = model.get<void*>(hero, "_heroState");
            void* effects = hero_state ? model.get<void*>(hero_state, "AppliedEffects") : nullptr;
            for (int j = 0; effects && j < model.get<int>(effects, "_size"); ++j) {
                void* effect = model.call(effects, "get_Item", {&j});
                void* type = model.get<void*>(effect, "_type");
                if (!type || model.get<int>(type, "KindId") != 9020) continue;
                const int apply_turn = model.get<int>(effect, "ApplyTurn");
                const int id = model.get<int>(effect, "Id");
                const int actor = model.unbox<int>(model.call(hero, "get_Id"));
                if (!seen_hunger.insert(std::to_string(actor) + ":" + std::to_string(id) + ":" +
                                        std::to_string(apply_turn)).second) continue;
                if (!first_hunger) hunger_events << ',';
                first_hunger = false;
                hunger_events << "{\"applyAtTurn\":" << model.get<int>(state, "CurrentTurn")
                              << ",\"actorId\":" << actor
                              << ",\"heroTypeId\":" << model.get<int>(hero, "TypeId")
                              << ",\"inventoryHeroId\":" << model.get<int>(hero, "InventoryHeroId")
                              << ",\"appliedEffectId\":" << id << ",\"applyTurn\":" << apply_turn
                              << ",\"effectTypeId\":" << model.get<int>(effect, "EffectTypeId") << '}';
            }
        }
    };
    std::ostringstream result_hunger_events;
    result_hunger_events << '[';
    bool first_result_hunger = true;
    // Devoured (effect kind 9024): the hero actually swallowed by a head, as
    // opposed to only marked; from the same processor result stream.
    std::ostringstream result_swallow_events;
    result_swallow_events << '[';
    bool first_result_swallow = true;
    auto record_result_hunger = [&](void* processor_results, int command_index) {
        for (int i = 0; processor_results && i < model.get<int>(processor_results, "_size"); ++i) {
            void* processor_result = model.call(processor_results, "get_Item", {&i});
            void* actions = model.get<void*>(processor_result, "Results");
            for (int j = 0; actions && j < model.get<int>(actions, "_size"); ++j) {
                void* action = model.call(actions, "get_Item", {&j});
                void* applied = model.call(action, "get_AppliedEffect");
                if (!applied || !model.get<bool>(applied, "IsApplied")) continue;
                void* effect = model.get<void*>(applied, "Effect");
                void* target = model.get<void*>(applied, "Target");
                void* type = effect ? model.get<void*>(effect, "_type") : nullptr;
                const int kind = type ? model.get<int>(type, "KindId") : 0;
                if (target && kind == 9024) {
                    if (!first_result_swallow) result_swallow_events << ',';
                    first_result_swallow = false;
                    result_swallow_events << "{\"commandIndex\":" << command_index
                                          << ",\"turn\":" << model.get<int>(processor_result, "Turn")
                                          << ",\"actorId\":" << model.unbox<int>(model.call(target, "get_Id"))
                                          << ",\"effectId\":" << model.get<int>(effect, "Id")
                                          << ",\"applyTurn\":" << model.get<int>(effect, "ApplyTurn") << '}';
                    continue;
                }
                if (!target || kind != 9020) continue;
                if (!first_result_hunger) result_hunger_events << ',';
                first_result_hunger = false;
                result_hunger_events << "{\"commandIndex\":" << command_index
                                     << ",\"processorIndex\":" << i << ",\"actionIndex\":" << j
                                     << ",\"turn\":" << model.get<int>(processor_result, "Turn")
                                     << ",\"actorId\":" << model.unbox<int>(model.call(target, "get_Id"))
                                     << ",\"effectId\":" << model.get<int>(effect, "Id")
                                     << ",\"effectTypeId\":" << model.get<int>(effect, "EffectTypeId")
                                     << ",\"applyTurn\":" << model.get<int>(effect, "ApplyTurn") << '}';
            }
        }
    };
    record_hunger();
    record_result_hunger(start_results, -1);

    checkpoint("prepare_policy_projection");
    OfflinePolicyProjection projection(model, static_data, setup);
    // Every actor the battle ever had (killed heads are replaced by new ones),
    // and who is dead now: per-command deaths for the simulation report.
    struct ActorSeen { int type_id; bool player; int inventory_id; };
    std::map<int, ActorSeen> actors_seen;
    auto dead_flags = [&] {
        std::map<int, bool> dead;
        for (const char* team_name : {"FirstTeam", "SecondTeam"}) {
            void* team = model.get<void*>(state, team_name);
            void* members = team ? model.get<void*>(team, "Heroes") : nullptr;
            for (int i = 0; members && i < model.get<int>(members, "_size"); ++i) {
                void* hero = model.call(members, "get_Item", {&i});
                void* hero_state = hero ? model.get<void*>(hero, "_heroState") : nullptr;
                if (!hero) continue;
                const int id = model.unbox<int>(model.call(hero, "get_Id"));
                actors_seen.emplace(id, ActorSeen{model.get<int>(hero, "TypeId"),
                                                  std::string(team_name) == "FirstTeam",
                                                  model.get<int>(hero, "InventoryHeroId")});
                dead[id] = hero_state && model.get<bool>(hero_state, "IsDead");
            }
        }
        return dead;
    };
    auto dead_before = dead_flags();
    void* enemy_generator = nullptr;
    std::uint64_t policy_requests = 0;
    std::uint64_t policy_commands = 0;
    std::string stop_reason;
    std::string stop_detail;
    std::ostringstream turns;
    turns.precision(15);  // Hydra damage exceeds the default six digits.
    turns << '[';
    int commands = 0;
    const auto player_owner = model.get<std::int64_t>(model.get<void*>(setup, "FirstTeam"), "TeamOwnerId");
    // A game turn and a submitted skill command are separate quantities.
    // The command bound only guards against a non-advancing simulation.
    constexpr int max_skill_commands = 20000;
    PhaseTiming timing;
    // Damage to a head as the Hydra counter counts it (the HP it lost; shields excluded).
    CommandBreakdown breakdown(model, [&](void* head) { return projection.hydra_taken(head); });
    breakdown.observe(state);
    while (!model.get<bool>(state, "BattleFinished") &&
           model.get<int>(state, "CurrentTurn") < max_game_turn &&
           commands < max_skill_commands) {
        // Everything rooted during one command is released at its end; a
        // thousand-turn battle otherwise exhausts the worker's memory limit.
        std::size_t root_frame = model.root_mark();
        if (commands % 50 == 0) {
            const std::string progress = "execute_original_skill_command_turn_" +
                std::to_string(model.get<int>(state, "CurrentTurn")) + "_command_" + std::to_string(commands);
            checkpoint(progress.c_str());
        }
        void* active = model.get<void*>(state, "ActiveHero");
        if (!active) throw std::runtime_error("Original battle has no active hero before a skill command");
        const int active_id = model.unbox<int>(model.call(active, "get_Id"));
        const int active_type_id = model.get<int>(active, "TypeId");
        const bool player_action = model.unbox<std::int64_t>(model.call(active, "get_OwnerUserId")) == player_owner;
        const int turn_before = model.get<int>(state, "CurrentTurn");
        const int round_before = model.get<int>(state, "CurrentRound");
        const int player_turns_before = model.get<int>(state, "PlayerTurnCount");
        const auto rng_before = offline_rng_words(model, random);
        void* command = nullptr;
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
            const PolicyReply reply = policy.receive(request);
            timing.lap(PhaseTiming::kPolicyWait);
            if (!reply.command) {
                stop_reason = "policy_stopped";
                stop_detail = reply.reason;
                break;
            }
            try {
                command = make_original_player_command_from_input(
                    model, static_data, state, player_owner, reply.actor_id,
                    reply.skill_type_id, reply.target_id, true,
                    "Policy command " + std::to_string(request));
            } catch (const std::exception& error) {
                stop_reason = "policy_command_rejected";
                stop_detail = error.what();
                break;
            }
            ++policy_commands;
            timing.lap(PhaseTiming::kPlayerCommand);
        } else {
            try {
                if (!enemy_generator) {
                    void* generator_class = model.klass(
                        "SharedModel.Battle.AI.TurnActionGeneration", "EnemyTurnActionGenerator");
                    enemy_generator = model.allocate(generator_class);
                    model.invoke(model.method(generator_class, ".ctor", 1), enemy_generator, {context});
                    // The generator lives for the whole battle: keep its roots.
                    root_frame = model.root_mark();
                }
                command = make_original_enemy_ai_command(model, static_data, context, enemy_generator, active);
            } catch (const std::exception& error) {
                stop_reason = "enemy_ai_command_unavailable";
                stop_detail = error.what();
                break;
            }
        }
        timing.lap(PhaseTiming::kEnemyAi);
        if (!command || model.get<int>(command, "Type") != 0 || model.get<void*>(command, "CheatCommand"))
            throw std::runtime_error("Original battle did not produce an ordinary skill command");
        void* skill = model.get<void*>(command, "SkillCommand");
        if (!skill) throw std::runtime_error("Original skill command is missing");
        void* target = model.get<void*>(skill, "Target");
        if (commands) turns << ',';
        turns << "{\"turnBefore\":" << turn_before << ",\"roundBefore\":" << round_before
              << ",\"playerTurnCountBefore\":" << player_turns_before
              << ",\"actorId\":" << active_id << ",\"actorTypeId\":" << active_type_id
              << ",\"playerAction\":" << (player_action ? "true" : "false")
              << ",\"skillTypeId\":" << model.get<int>(skill, "SkillTypeId")
              << ",\"targetId\":" << (target ? model.get<int>(target, "HeroId") : -1)
              << ",\"rngBefore\":[" << rng_before[0] << ',' << rng_before[1] << ','
              << rng_before[2] << ',' << rng_before[3] << ']';
        timing.lap(PhaseTiming::kOther);
        void* processed = model.call(processor, "ApplyCommand", {command});
        timing.lap(PhaseTiming::kApply);
        record_result_hunger(processed, commands);
        record_hunger();
        const auto rng_after = offline_rng_words(model, random);
        const auto dead_after = dead_flags();
        turns << ",\"turnAfter\":" << model.get<int>(state, "CurrentTurn") << ",\"rngAfter\":["
              << rng_after[0] << ',' << rng_after[1] << ',' << rng_after[2] << ',' << rng_after[3] << ']'
              << ",\"damageAfter\":" << projection.hydra_damage(state) << ",\"deaths\":[";
        bool first_death = true;
        for (const auto& [id, dead] : dead_after) {
            const auto found = dead_before.find(id);
            if (dead == (found != dead_before.end() && found->second)) continue;
            if (!first_death) turns << ',';
            first_death = false;
            turns << (dead ? id : -1 - id);  // negative: revived
        }
        turns << "],\"uses\":";
        breakdown.append(turns, processed, state);
        turns << '}';
        dead_before = dead_after;
        ++commands;
        model.release_roots_since(root_frame);
    }
    turns << ']';
    hunger_events << ']';
    result_hunger_events << ']';
    result_swallow_events << ']';
    const bool battle_finished = model.get<bool>(state, "BattleFinished");
    const int game_turn = model.get<int>(state, "CurrentTurn");
    const bool stopped_early = !stop_reason.empty();
    if (!stopped_early)
        stop_reason = battle_finished ? "battle_finished" :
                      (game_turn >= max_game_turn ? "game_turn_limit" : "skill_command_limit");
    for (char& character : stop_detail)
        if (character == '"' || character == '\\' || static_cast<unsigned char>(character) < 32)
            character = '_';

    checkpoint("collect_original_battle_result");
    void* battle_result = battle_finished ? model.call(processor, "GetBattleResult") : nullptr;
    if (battle_finished && !battle_result) throw std::runtime_error("Original processor returned no battle result");
    std::string setup_id;
    constexpr char digits[] = "0123456789abcdef";
    for (auto byte : model.get<std::array<unsigned char, 16>>(setup, "Id")) {
        setup_id += digits[byte >> 4];
        setup_id += digits[byte & 15];
    }
    std::ostringstream player_actors;
    player_actors << '[';
    void* heroes = model.get<void*>(model.get<void*>(state, "FirstTeam"), "Heroes");
    for (int i = 0; heroes && i < model.get<int>(heroes, "_size"); ++i) {
        void* hero = model.call(heroes, "get_Item", {&i});
        if (i) player_actors << ',';
        player_actors << "{\"actorId\":" << model.unbox<int>(model.call(hero, "get_Id"))
                      << ",\"heroTypeId\":" << model.get<int>(hero, "TypeId")
                      << ",\"inventoryHeroId\":" << model.get<int>(hero, "InventoryHeroId")
                      << ",\"slotId\":" << model.get<int>(hero, "SlotId") << '}';
    }
    player_actors << ']';
    std::ostringstream all_actors;
    all_actors << '[';
    const auto dead_now = dead_flags();
    bool first_actor = true;
    for (const auto& [id, seen] : actors_seen) {
        const auto found = dead_now.find(id);
        if (!first_actor) all_actors << ',';
        first_actor = false;
        // An actor no longer in its team (a replaced head) counts as dead.
        all_actors << "{\"actorId\":" << id << ",\"heroTypeId\":" << seen.type_id
                   << ",\"inventoryHeroId\":" << seen.inventory_id
                   << ",\"player\":" << (seen.player ? "true" : "false")
                   << ",\"dead\":" << (found == dead_now.end() || found->second ? "true" : "false") << '}';
    }
    all_actors << ']';

    std::ostringstream result;
    result.precision(15);
    result << "\"policyDriven\":true"
           << ",\"enemyCommandPath\":\"EnemyTurnActionGenerator.GenerateNext\""
           << ",\"playerCooldownHypothesis\":true,\"enemyCooldownHypothesis\":true"
           << ",\"policyRequests\":" << policy_requests
           << ",\"policyCommands\":" << policy_commands
           << ",\"phaseMs\":" << timing.json()
           << ",\"hydraDamage\":" << projection.hydra_damage(state)
           << ",\"policyStop\":"
           << (stopped_early ? "{\"reason\":\"" + stop_reason + "\",\"detail\":\"" + stop_detail + "\"}"
                             : std::string("null"))
           << ",\"engineVersion\":" << active_engine_version
           << ",\"seed\":" << model.get<int>(setup, "RandomSeed")
           << ",\"capturedSeed\":" << captured_seed
           << ",\"seedOverridden\":" << (seed_override ? "true" : "false")
           << ",\"battleSetupId\":\"" << setup_id << '"'
           << ",\"playerActors\":" << player_actors.str()
           << ",\"actors\":" << all_actors.str()
           << ",\"battleKindId\":5,\"stageId\":" << model.get<int>(setup, "StageId")
           << ",\"turn\":" << game_turn
           << ",\"configuredMaxTurnsInBattle\":" << configured_max_turns
           << ",\"effectiveMaxTurnsInBattle\":" << effective_max_turns
           << ",\"maxGameTurn\":" << max_game_turn
           << ",\"stopReason\":\"" << stop_reason << '"'
           << ",\"elapsedMs\":" << (GetTickCount64() - battle_started_at)
           << ",\"battleFinished\":" << (battle_finished ? "true" : "false")
           << ",\"resultType\":" << (battle_result ? std::to_string(model.get<int>(battle_result, "ResultType")) : "null")
           << ",\"finishCause\":" << (battle_result ? std::to_string(model.get<int>(battle_result, "FinishCause")) : "null")
           << ",\"commands\":" << commands
           << ",\"hungerEvents\":" << hunger_events.str()
           << ",\"resultHungerEvents\":" << result_hunger_events.str()
           << ",\"resultSwallowEvents\":" << result_swallow_events.str()
           << ",\"turns\":" << turns.str();
    return result.str();
}
