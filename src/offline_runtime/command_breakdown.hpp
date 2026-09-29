#pragma once

#include "managed_runtime.hpp"

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

// What one command set off, from ApplyCommand's processor results: every skill
// use (the command's own skill, ally attacks, counterattacks, provoked attacks,
// skills activated by effects, passives that dealt damage) with the damage its
// user dealt to the other team, plus damage no skill use accounts for (damage
// over time at turn start, effects placed by another hero), per dealer.
//
// JSON: [[actorId, skillTypeId, targetId, "trigger", damage], ...] where trigger
// is input | team | counter | provoke | activate | effect | passive | other
// (other: skillTypeId 0, targetId -1; actorId -1 when no hero dealt it, e.g.
// damage reflected onto an attacker).
//
// Damage to the boss side is what the game counts: each damage record (the
// DamageResult value, before shields) is reconciled per target with the
// counted damage the caller reads (the Hydra counter per head, the Chimera's
// damage statistics). A shield takes the first hits until it breaks, so when a
// target counted less than its records, the counted damage goes to its latest
// hits; counted damage beyond the records has no known dealer (-1).
class CommandBreakdown {
public:
    // Cumulative counted damage of a boss-side unit, or a negative value when unknown.
    using Counted = std::function<double(void* unit)>;

    explicit CommandBreakdown(const ManagedRuntime& model, Counted counted = {})
        : model_(model), counted_(std::move(counted)) {}

    // The counted damage of every boss-side unit now: call once before the first command.
    void observe(void* state) {
        counted_before_.clear();
        if (!counted_) return;
        void* team = model_.get<void*>(state, "SecondTeam");
        void* units = team ? model_.get<void*>(team, "Heroes") : nullptr;
        for (int i = 0; i < size(units); ++i) {
            void* unit = item(units, i);
            counted_before_[actor_id(unit)] = counted_(unit);
        }
    }

    void append(std::ostream& out, void* processed, void* state) {
        std::set<int> players;
        void* team = model_.get<void*>(state, "FirstTeam");
        void* heroes = team ? model_.get<void*>(team, "Heroes") : nullptr;
        for (int i = 0; i < size(heroes); ++i) players.insert(actor_id(item(heroes, i)));
        struct Use { int actor, skill, target; const char* trigger; double damage; };
        // One damage record: its target, the use it belongs to (-1: other damage) and its dealer.
        struct Record { int target, use, dealer; double amount; };
        std::vector<Use> uses;
        std::vector<Record> records;
        for (int i = 0; i < size(processed); ++i) {
            void* result = item(processed, i);
            void* info = model_.get<int>(result, "ResultType") == value(result, "ResultType", "ApplySkill")
                ? model_.get<void*>(result, "<SkillInfo>k__BackingField") : nullptr;
            int user = -1;
            if (info) {
                void* skill = optional(info, "Skill");
                user = actor_id(optional(info, "Producer"));
                uses.push_back({user, skill ? model_.get<int>(skill, "TypeId") : 0,
                                actor_id(optional(info, "Target")), trigger(optional(info, "Source")), 0.0});
            }
            void* actions = model_.get<void*>(result, "Results");
            for (int j = 0; j < size(actions); ++j) {
                void* action = item(actions, j);
                if (model_.get<int>(action, "_resultType") != value(action, "_resultType", "CalculatedDamage")) continue;
                void* damage = model_.get<void*>(action, "_result");
                if (!damage || !has(damage, "_value")) continue;
                const int to = actor_id(model_.get<void*>(action, "Target"));
                const double amount = static_cast<double>(model_.get<std::int64_t>(damage, "_value")) / 4294967296.0;
                if (to < 0 || amount <= 0.0) continue;
                // The effect's owner dealt it (damage over time, a detonation); damage an
                // enemy redirected to its own side (ally protection) counts for the hero
                // whose skill caused it.
                void* context = model_.get<void*>(action, "EffectDescription");
                int by = actor_id(optional(optional(context, "ApplyContext"), "Producer"));
                if (by < 0 || players.count(by) == players.count(to))
                    by = actor_id(optional(optional(context, "SkillContext"), "Producer"));
                if (by < 0 || players.count(by) == players.count(to)) {
                    if (!players.count(to)) records.push_back({to, -1, -1, amount});  // no hero as dealer
                    continue;
                }
                const bool own = info && by == user;
                records.push_back({to, own ? static_cast<int>(uses.size()) - 1 : -1, by, amount});
            }
        }
        std::map<int, double> other;
        reconcile(records, players, state, other);
        for (const Record& record : records) {
            if (record.use >= 0) uses[record.use].damage += record.amount;
            else other[record.dealer] += record.amount;
        }
        out << '[';
        bool first = true;
        auto entry = [&](int actor, int skill, int target, const char* kind, double damage) {
            out << (first ? "" : ",") << '[' << actor << ',' << skill << ',' << target << ",\"" << kind << "\","
                << damage << ']';
            first = false;
        };
        for (const Use& use : uses)
            if (std::string(use.trigger) != "passive" || use.damage > 0.0)
                entry(use.actor, use.skill, use.target, use.trigger, use.damage);
        for (const auto& [actor, damage] : other)
            if (damage > 0.0) entry(actor, 0, -1, "other", damage);
        out << ']';
    }

private:
    const ManagedRuntime& model_;
    Counted counted_;
    std::unordered_map<int, double> counted_before_;
    // Enum values by name, read once per enum field.
    std::unordered_map<std::string, std::unordered_map<std::string, int>> enums_;
    // BattleHero.Id (an auto property's backing field, the value get_Id returns):
    // its offset, found once per hero class.
    std::unordered_map<void*, std::size_t> id_offsets_;

    // Records to each boss-side unit made to add up to what the game counted for it.
    template <typename Records>
    void reconcile(Records& records, const std::set<int>& players, void* state, std::map<int, double>& other) {
        if (!counted_) return;
        const auto before = counted_before_;
        observe(state);
        std::map<int, double> recorded;
        for (const auto& record : records)
            if (!players.count(record.target)) recorded[record.target] += record.amount;
        for (const auto& [unit, after] : counted_before_) {
            const auto previous = before.find(unit);
            if (after < 0.0 || (previous != before.end() && previous->second < 0.0)) continue;
            const double counted = after - (previous == before.end() ? 0.0 : previous->second);
            const auto found = recorded.find(unit);
            const double total = found == recorded.end() ? 0.0 : found->second;
            if (std::abs(counted - total) <= 0.5 + 1e-9 * total) continue;
            if (counted > total) {
                other[-1] += counted - total;
                continue;
            }
            double remaining = (std::max)(counted, 0.0);
            for (auto record = records.rbegin(); record != records.rend(); ++record) {
                if (record->target != unit) continue;
                record->amount = (std::min)(record->amount, remaining);
                remaining -= record->amount;
            }
        }
    }

    // List<T> of references: size and items read in place (no managed calls).
    int size(void* list) const { return list ? model_.get<int>(list, "_size") : 0; }
    void* item(void* list, int index) const {
        void* items = model_.get<void*>(list, "_items");
        return *reinterpret_cast<void**>(static_cast<unsigned char*>(items) + 32 + static_cast<std::size_t>(index) * sizeof(void*));
    }

    int actor_id(void* hero) {
        if (!hero) return -1;
        void* klass = model_.object_class(hero);
        auto found = id_offsets_.find(klass);
        if (found == id_offsets_.end()) {
            std::size_t offset = 0;
            for (void* current = klass; current && !offset;
                 current = model_.api<void* (*)(void*)>("il2cpp_class_get_parent")(current)) {
                for (const char* name : {"<Id>k__BackingField", "Id"}) {
                    void* field = model_.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(current, name);
                    if (field && !offset) offset = model_.api<std::size_t (*)(void*)>("il2cpp_field_get_offset")(field);
                }
            }
            if (!offset) {
                const char* name = model_.api<const char* (*)(void*)>("il2cpp_class_get_name")(klass);
                throw std::runtime_error(std::string("Battle hero has no Id field: ") + (name ? name : "?"));
            }
            found = id_offsets_.emplace(klass, offset).first;
        }
        return *reinterpret_cast<const int*>(static_cast<const unsigned char*>(hero) + found->second);
    }

    bool has(void* object, const char* name) const {
        if (!object) return false;
        void* type = model_.object_class(object);
        return model_.cached(type, name, ManagedRuntime::kFieldLookup, [&] {
            return model_.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(type, name);
        }) != nullptr;
    }

    void* optional(void* object, const char* name) const { return has(object, name) ? model_.get<void*>(object, name) : nullptr; }

    int value(void* object, const char* field, const char* name) {
        auto found = enums_.find(field);
        if (found == enums_.end()) {
            auto& values = enums_[field];
            void* type = model_.api<void* (*)(void*)>("il2cpp_field_get_type")(model_.field(object, field));
            void* klass = model_.api<void* (*)(void*)>("il2cpp_class_from_type")(type);
            void* iterator = nullptr;
            while (void* constant = model_.api<void* (*)(void*, void**)>("il2cpp_class_get_fields")(klass, &iterator)) {
                if (!(model_.api<int (*)(void*)>("il2cpp_field_get_flags")(constant) & 0x10)) continue;  // static literals
                std::int64_t stored = 0;
                model_.api<void (*)(void*, void*)>("il2cpp_field_static_get_value")(constant, &stored);
                values[model_.api<const char* (*)(void*)>("il2cpp_field_get_name")(constant)] = static_cast<int>(stored);
            }
            found = enums_.find(field);
        }
        const auto match = found->second.find(name);
        return match == found->second.end() ? INT_MIN : match->second;
    }

    const char* trigger(void* source) {
        if (!source) return "passive";
        const int type = model_.get<int>(source, "Type");
        if (type == value(source, "Type", "Input")) return "input";
        if (type != value(source, "Type", "Effect")) return "passive";
        void* effect = optional(optional(source, "Effect"), "Effect");
        if (!effect) return "effect";
        const int kind = model_.get<int>(effect, "KindId");
        if (kind == value(effect, "KindId", "TeamAttack")) return "team";
        if (kind == value(effect, "KindId", "StatusCounterattack") ||
            kind == value(effect, "KindId", "PassiveCounterattack")) return "counter";
        if (kind == value(effect, "KindId", "Provoke")) return "provoke";
        if (kind == value(effect, "KindId", "ActivateSkill")) return "activate";
        return "effect";
    }
};
