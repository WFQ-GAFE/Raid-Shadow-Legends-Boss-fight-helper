#pragma once

// Original-engine helpers shared by the forecast: RNG words, actor lookup and
// the player command built through SkillCommand.FromInput, as the client does.

#include "managed_runtime.hpp"

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>

inline std::array<std::uint32_t, 4> offline_rng_words(const ManagedRuntime& model, void* random) {
    if (!random) throw std::runtime_error("Original battle has no random state");
    return {model.get<std::uint32_t>(random, "_x"), model.get<std::uint32_t>(random, "_y"),
            model.get<std::uint32_t>(random, "_z"), model.get<std::uint32_t>(random, "_w")};
}

inline void* find_original_battle_hero(const ManagedRuntime& model, void* state, int actor_id) {
    for (const char* team_name : {"FirstTeam", "SecondTeam"}) {
        void* team = model.get<void*>(state, team_name);
        void* heroes = team ? model.get<void*>(team, "Heroes") : nullptr;
        if (!heroes) continue;
        for (int i = 0; i < model.get<int>(heroes, "_size"); ++i) {
            void* hero = model.call(heroes, "get_Item", {&i});
            if (model.unbox<int>(model.call(hero, "get_Id")) == actor_id) return hero;
        }
    }
    return nullptr;
}

inline void* make_original_player_command_from_input(const ManagedRuntime& model, void* static_data,
                                                      void* state, std::int64_t player_owner,
                                                      int actor_id, int skill_type_id, int target_id,
                                                      bool set_on_cooldown, const std::string& label) {
    void* active = model.get<void*>(state, "ActiveHero");
    if (!active || model.unbox<int>(model.call(active, "get_Id")) != actor_id ||
        model.unbox<std::int64_t>(model.call(active, "get_OwnerUserId")) != player_owner)
        throw std::runtime_error(label + " actor or owner changed before command construction");
    void* skills = model.get<void*>(active, "Skills");
    bool ready = false;
    if (skills) for (int i = 0; i < model.get<int>(skills, "_size"); ++i) {
        void* candidate = model.call(skills, "get_Item", {&i});
        if (model.get<int>(candidate, "TypeId") == skill_type_id &&
            model.unbox<bool>(model.call(candidate, "get_IsReady"))) {
            ready = true;
            break;
        }
    }
    if (!ready) throw std::runtime_error(label + " skill is absent or not ready");
    void* target = target_id == -1 ? nullptr : find_original_battle_hero(model, state, target_id);
    if (target_id != -1 && !target)
        throw std::runtime_error(label + " target is not in the battle");
    void* types = model.call(model.get<void*>(static_data, "SkillData"), "get_SkillTypeById");
    void* skill_type = model.call(types, "get_Item", {&skill_type_id});
    void* skill_command = model.invoke(
        model.method(model.klass("SharedModel.Battle.Core.Commands", "SkillCommand"), "FromInput", 5),
        nullptr, {skill_type, active, target, &set_on_cooldown, nullptr});
    if (!skill_command || model.get<int>(skill_command, "SkillTypeId") != skill_type_id ||
        model.get<bool>(skill_command, "SetOnCooldown") != set_on_cooldown)
        throw std::runtime_error(label + " original FromInput did not preserve the skill or cooldown flag");
    void* produced = model.get<void*>(skill_command, "Producer");
    void* selected_target = model.get<void*>(skill_command, "Target");
    if (!produced || model.get<int>(produced, "HeroId") != actor_id ||
        (selected_target ? model.get<int>(selected_target, "HeroId") : -1) != target_id)
        throw std::runtime_error(label + " original FromInput did not preserve actor or target");
    void* command = model.allocate("SharedModel.Battle.Core.Commands", "BattleCommand", true);
    model.set(command, "Type", 0);
    model.set(command, "UserId", player_owner);
    model.set(command, "SkillCommand", skill_command);
    if (model.get<void*>(command, "CheatCommand"))
        throw std::runtime_error(label + " constructed command unexpectedly contains a cheat");
    return command;
}
