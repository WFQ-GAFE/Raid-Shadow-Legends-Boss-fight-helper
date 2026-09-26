#pragma once

#include "managed_runtime.hpp"

#include <array>
#include <cstdint>
#include <stdexcept>

// Research-only enemy command path. The live client uses a turn-action
// generator for AI turns; BattleProcessor.GetDefaultSkill is merely a fallback
// and does not represent the head's decision. All objects are private to the
// isolated original runtime.
inline void* make_original_enemy_ai_command(const ManagedRuntime& model,
                                             void* static_data, void* context,
                                             void* generator, void* active) {
    std::array<std::uint8_t, 8> no_focused_hero{}; // Nullable<Int32> with HasValue=false.
    void* action = model.call(generator, "GenerateNext", {context, no_focused_hero.data()});
    if (!action) throw std::runtime_error("Original enemy AI returned no turn action");
    void* skill = model.get<void*>(action, "Skill");
    void* producer = model.get<void*>(action, "Producer");
    void* target = model.get<void*>(action, "Target");
    if (!skill || !producer || producer != active)
        throw std::runtime_error("Original enemy AI action is missing or has another producer");
    if (!model.unbox<bool>(model.call(skill, "get_IsReady")))
        throw std::runtime_error("Original enemy AI chose a skill that is not ready");
    int skill_id = model.get<int>(skill, "TypeId");
    void* types = model.call(model.get<void*>(static_data, "SkillData"), "get_SkillTypeById");
    void* skill_type = model.call(types, "get_Item", {&skill_id});
    if (!skill_type) throw std::runtime_error("Original enemy AI skill type is absent");
    // The client's ClientCommandGenerator.MakeSkillCommand(TurnAction) and
    // CreateCmdManually both pass the constant true (`mov r9b,1` before the
    // SkillCommand.FromInput call in the 11.75.0 GameAssembly.dll), matching
    // the historical RNG checkpoints.
    bool set_on_cooldown = true;
    void* skill_command = model.invoke(
        model.method(model.klass("SharedModel.Battle.Core.Commands", "SkillCommand"), "FromInput", 5),
        nullptr, {skill_type, producer, target, &set_on_cooldown, nullptr});
    if (!skill_command || model.get<int>(skill_command, "SkillTypeId") != skill_id)
        throw std::runtime_error("Original enemy AI command did not retain its skill");
    void* command = model.allocate("SharedModel.Battle.Core.Commands", "BattleCommand", true);
    model.set(command, "Type", 0);
    model.set(command, "UserId", model.unbox<std::int64_t>(
        model.call(producer, "get_OwnerUserId")));
    model.set(command, "SkillCommand", skill_command);
    if (model.get<void*>(command, "CheatCommand"))
        throw std::runtime_error("Original enemy AI produced a cheat command");
    return command;
}
