#pragma once

#include "managed_runtime.hpp"
#include "battle_commands.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <cstdint>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

// Controller-format decision_state (Hydra KindId 5 or Chimera KindId 8)
// built from the isolated original engine at a player command window. Field
// names, derivations and number formatting mirror the live agent's
// capture_decision_state so the unchanged controller policy sees the same
// inputs. Static per-difficulty data the agent attaches (trialCatalog,
// rotationIdentity, names) is added by the caller. Nothing here touches a
// live game.
class OfflinePolicyProjection {
public:
    OfflinePolicyProjection(const ManagedRuntime& model, void* static_data, void* setup)
        : model_(model), static_data_(static_data), setup_(setup) {
        if (!static_data_ || !setup_)
            throw std::runtime_error("Policy projection needs static data and setup");
        skill_types_ = model_.call(model_.get<void*>(static_data_, "SkillData"), "get_SkillTypeById");
        hero_types_ = model_.get<void*>(model_.get<void*>(static_data_, "HeroData"), "HeroTypeById");
        hydra_damage_ = model_.method_exact(
            model_.klass("SharedModel.Battle.Extensions", "HydraExtensions"),
            "HydraTotalTakenDamage", {"SharedModel.Battle.Core.Hero.BattleHero"});
        selector_ = model_.method_exact(
            model_.klass("SharedModel.Battle.Core.TargetSelection", "SkillTargetSelector"),
            "GetTargets", {
                "System.Collections.Generic.IEnumerable<SharedModel.Battle.Core.Hero.BattleHero>",
                "SharedModel.Battle.Core.Hero.BattleHero",
                "SharedModel.Meta.Skills.SkillTargets",
                "SharedModel.Meta.Skills.SkillType",
            });
        load_effect_kind_names();
        chimera_ = model_.get<int>(setup_, "KindId") == 8;
        if (chimera_) load_chimera_static();
        constexpr char digits[] = "0123456789abcdef";
        for (const auto byte : model_.get<std::array<unsigned char, 16>>(setup_, "Id")) {
            setup_id_ += digits[byte >> 4];
            setup_id_ += digits[byte & 15];
        }
        seed_ = model_.get<int>(setup_, "RandomSeed");
        stage_id_ = model_.get<int>(setup_, "StageId");
    }

    const std::string& setup_id() const { return setup_id_; }
    bool chimera() const { return chimera_; }
    // Entries of a Dictionary<int, T>: key and the address of the stored value.
    std::vector<std::pair<int, void*>> entries(void* dictionary) const { return dictionary_entries(dictionary); }

    // Chimera damage as the agent reports it: the sum of every multiplier's
    // ChimeraMultiplierStatistics.Damage, as a double of the Q32.32 total.
    double chimera_damage(void* state) const {
        void* statistics = model_.get<void*>(state, "ChimeraStatisticsByMultiplier");
        if (!statistics) return 0.0;
        std::int64_t total = 0;
        for (void* entry : dictionary_values(statistics)) total += model_.get<std::int64_t>(entry, "Damage");
        return static_cast<double>(total) / 4294967296.0;
    }

    std::size_t chimera_statistics_count(void* state) const {
        void* statistics = model_.get<void*>(state, "ChimeraStatisticsByMultiplier");
        return statistics ? dictionary_values(statistics).size() : 0;
    }

    // Battle damage as the Hydra UI counter shows it: each head's integer
    // HydraTotalTakenDamage, summed over every head the battle has had
    // (killed and replaced heads keep their contribution). Summing raw
    // Q32.32 values would overflow at this scale.
    std::int64_t hydra_damage(void* state) const {
        void* team = model_.get<void*>(state, "SecondTeam");
        void* heads = team ? model_.get<void*>(team, "Heroes") : nullptr;
        if (!heads) throw std::runtime_error("Hydra damage needs the enemy team");
        std::int64_t total = 0;
        for (int i = 0; i < model_.get<int>(heads, "_size"); ++i) {
            void* head = model_.call(heads, "get_Item", {&i});
            const std::int64_t raw = model_.unbox<std::int64_t>(model_.invoke(hydra_damage_, nullptr, {head}));
            if (raw > 0) total += raw >> 32;
        }
        return total;
    }

    // Returns one decision_state JSON object. Throws on any required field the
    // original model does not expose; the caller turns that into "unknown".
    std::string decision_state(void* state, std::uint64_t sequence) {
        void* active = model_.get<void*>(state, "ActiveHero");
        void* random = model_.get<void*>(state, "Random");
        if (!active || !random)
            throw std::runtime_error("Policy projection needs an active hero and RNG");
        const int turn = model_.get<int>(state, "CurrentTurn");
        const int round = model_.get<int>(state, "CurrentRound");
        const int player_turns = model_.get<int>(state, "PlayerTurnCount");
        const int active_id = actor_id(active);
        const int active_type = model_.get<int>(active, "TypeId");
        const int active_form = model_.get<int>(active, "CurrentFormIndex");
        bool metamorph = false;
        optional_bool_method(active, "get_IsMetamorph", metamorph);
        const auto rng = offline_rng_words(model_, random);

        std::ostringstream out;
        out << "{\"type\":\"decision_state\",\"source\":\"offline_original_engine\""
            << ",\"sequence\":" << sequence
            << ",\"bossMode\":\"" << (chimera_ ? "chimera" : "hydra") << "\",\"battleGeneration\":1"
            << ",\"battle\":{\"kindId\":" << model_.get<int>(setup_, "KindId")
            << ",\"round\":" << round << ",\"turn\":" << turn
            << ",\"playerTurnCount\":" << player_turns
            << ",\"finished\":" << boolean(model_.get<bool>(state, "BattleFinished"))
            << ",\"autoMode\":false"
            << ",\"chimeraPreset\":" << boolean(model_.get<bool>(state, "IsChimeraPreset"))
            << ",\"hydraBattle\":" << boolean(!chimera_)
            << ",\"extraTurn\":" << boolean(model_.get<bool>(state, "IsExtraTurn"))
            << ",\"waitingForManualCommand\":true,\"metricsAvailable\":true";
        if (chimera_) {
            out << ",\"currentDamage\":" << chimera_damage(state)
                << ",\"metricsSource\":\"chimera_statistics\""
                << ",\"metricsSampleCount\":" << chimera_statistics_count(state) << '}';
        } else {
            out << ",\"metricsSource\":\"offline_hydra_total_taken_damage\""
                << ",\"currentDamage\":" << hydra_damage(state) << '}';
        }
        out
            << ",\"activeHeroId\":" << active_id
            << ",\"activeHeroTypeId\":" << active_type
            << ",\"activeHeroTurnCount\":" << model_.get<int>(active, "TurnCount")
            << ",\"activeHeroFormIndex\":" << active_form
            << ",\"activeHeroSkillsUpdateCounter\":" << model_.get<int>(active, "CurrentSkillsUpdateCounter")
            << ",\"activeHeroIsMetamorph\":" << boolean(metamorph)
            << ",\"activeHeroIsTransformed\":" << boolean(active_form != 0)
            << ",\"skillCatalogFresh\":true,\"skills\":";
        append_hud_skills(out, state, active);
        out << ",\"battleRandom\":{\"schema\":1,\"source\":\"BattleState.Random_fields\""
            << ",\"available\":true,\"readStatus\":\"offline_engine_exact\""
            << ",\"capturePoint\":\"offline_policy_window\""
            << ",\"turn\":" << turn << ",\"playerTurnCount\":" << player_turns
            << ",\"words\":[" << rng[0] << ',' << rng[1] << ',' << rng[2] << ',' << rng[3] << ']'
            << ",\"seedAvailable\":true,\"seed\":" << seed_
            << ",\"battleSetupIdAvailable\":true,\"battleSetupId\":\"" << setup_id_ << "\"}";
        out << ",\"heroes\":";
        append_team(out, state, "FirstTeam", "ally", false);
        out << ",\"bosses\":";
        append_team(out, state, "SecondTeam", "enemy", !chimera_);
        if (chimera_) {
            void* boss = model_.call(state, "Chimera");
            if (!boss) throw std::runtime_error("Chimera policy projection has no Chimera hero");
            const int form = model_.get<int>(boss, "CurrentFormIndex");
            const auto trials = dictionary_entries(model_.get<void*>(boss, "Challenges"));
            const int difficulty = trials.empty() ? 0 : trial_identity(trials.front().first).difficulty;
            out << ",\"chimera\":{\"id\":" << actor_id(boss)
                << ",\"typeId\":" << model_.get<int>(boss, "TypeId")
                << ",\"currentFormIndex\":" << form
                << ",\"currentForm\":\"" << escaped(enum_name(chimera_forms_, form)) << '"'
                << ",\"turnCount\":" << model_.get<int>(boss, "TurnCount") << '}'
                << ",\"hydra\":{\"active\":false,\"turnCount\":" << turn << '}'
                << ",\"allianceChimeraDifficultyId\":" << difficulty;
        } else {
            out << ",\"chimera\":{\"id\":0,\"typeId\":0,\"currentFormIndex\":0,\"currentForm\":\"\",\"turnCount\":0}"
                << ",\"hydra\":{\"active\":true,\"turnCount\":" << turn << '}'
                << ",\"allianceChimeraDifficultyId\":0";
        }
        out << ",\"chimeraStageId\":" << stage_id_ << '}';
        return out.str();
    }

private:
    const ManagedRuntime& model_;
    void* static_data_{};
    void* setup_{};
    void* skill_types_{};
    void* hero_types_{};
    void* selector_{};
    void* hydra_damage_{};
    std::string setup_id_;
    int seed_{};
    int stage_id_{};
    bool chimera_{};
    struct TrialIdentity { int difficulty{}, form{}, part{}, level{}; };
    std::unordered_map<int, TrialIdentity> trial_identities_;
    std::unordered_map<int, int> last_turn_by_form_;
    std::unordered_map<int, std::string> chimera_forms_, chimera_parts_, chimera_levels_;
    std::unordered_map<int, std::string> effect_kind_names_;
    std::map<std::pair<int, int>, std::string> avatars_;

    static const char* boolean(bool value) { return value ? "true" : "false"; }

    int actor_id(void* hero) const { return model_.unbox<int>(model_.call(hero, "get_Id")); }

    void* find_method(void* object, const char* name, int arguments) const {
        return model_.api<void* (*)(void*, const char*, int)>("il2cpp_class_get_method_from_name")(
            model_.object_class(object), name, arguments);
    }

    bool has_field(void* object, const char* name) const {
        return model_.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(
                   model_.object_class(object), name) != nullptr;
    }

    // The live agent treats these getters as optional; absent members stay false.
    void optional_bool_method(void* object, const char* name, bool& value) const {
        if (void* method = find_method(object, name, 0))
            value = model_.unbox<bool>(model_.invoke(method, object));
    }

    static std::string escaped(const std::string& value) {
        std::string result;
        for (const unsigned char c : value) {
            if (c == '"' || c == '\\') { result += '\\'; result += static_cast<char>(c); }
            else if (c < 0x20) {
                constexpr char hex[] = "0123456789abcdef";
                result += "\\u00";
                result += hex[c >> 4];
                result += hex[c & 15];
            } else result += static_cast<char>(c);
        }
        return result;
    }

    // Dictionary<int, TValue> entries in storage order, like the agent's
    // read_int_object_dictionary: skip freed slots (negative hash/key).
    std::vector<std::pair<int, void*>> dictionary_entries(void* dictionary) const {
        std::vector<std::pair<int, void*>> result;
        if (!dictionary) return result;
        void* entries = has_field(dictionary, "_entries") ? model_.get<void*>(dictionary, "_entries")
                                                          : model_.get<void*>(dictionary, "entries");
        const int count = has_field(dictionary, "_count") ? model_.get<int>(dictionary, "_count")
                                                          : model_.get<int>(dictionary, "count");
        if (!entries || count < 0 || count > 4096) return result;
        void* array_class = model_.object_class(entries);
        void* entry_class = model_.api<void* (*)(void*)>("il2cpp_class_get_element_class")(array_class);
        const auto size = static_cast<std::size_t>(
            model_.api<int (*)(void*)>("il2cpp_class_array_element_size")(entry_class));
        auto offset = [&](const char* name) -> std::size_t {
            void* field = model_.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(entry_class, name);
            if (!field) throw std::runtime_error(std::string("Dictionary entry has no ") + name);
            return model_.api<std::size_t (*)(void*)>("il2cpp_field_get_offset")(field) - 16;
        };
        const std::size_t hash = offset("hashCode"), key = offset("key"), value = offset("value");
        const auto length = model_.api<std::uintptr_t (*)(void*)>("il2cpp_array_length")(entries);
        auto* data = static_cast<unsigned char*>(entries) + 32;
        for (std::size_t i = 0; i < static_cast<std::size_t>(count) && i < length; ++i) {
            auto* entry = data + i * size;
            const int entry_hash = *reinterpret_cast<int*>(entry + hash);
            const int entry_key = *reinterpret_cast<int*>(entry + key);
            if (entry_hash < 0 || entry_key < 0) continue;
            result.emplace_back(entry_key, reinterpret_cast<void*>(entry + value));
        }
        return result;
    }

    // Values of a Dictionary<K, reference type>.
    std::vector<void*> dictionary_values(void* dictionary) const {
        std::vector<void*> result;
        void* entries = has_field(dictionary, "_entries") ? model_.get<void*>(dictionary, "_entries")
                                                          : model_.get<void*>(dictionary, "entries");
        const int count = has_field(dictionary, "_count") ? model_.get<int>(dictionary, "_count")
                                                          : model_.get<int>(dictionary, "count");
        if (!entries || count < 0 || count > 4096) return result;
        void* entry_class = model_.api<void* (*)(void*)>("il2cpp_class_get_element_class")(model_.object_class(entries));
        const auto size = static_cast<std::size_t>(
            model_.api<int (*)(void*)>("il2cpp_class_array_element_size")(entry_class));
        void* hash_field = model_.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(entry_class, "hashCode");
        void* value_field = model_.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(entry_class, "value");
        if (!hash_field || !value_field) throw std::runtime_error("Dictionary entry layout unavailable");
        const auto hash = model_.api<std::size_t (*)(void*)>("il2cpp_field_get_offset")(hash_field) - 16;
        const auto value = model_.api<std::size_t (*)(void*)>("il2cpp_field_get_offset")(value_field) - 16;
        const auto length = model_.api<std::uintptr_t (*)(void*)>("il2cpp_array_length")(entries);
        auto* data = static_cast<unsigned char*>(entries) + 32;
        for (std::size_t i = 0; i < static_cast<std::size_t>(count) && i < length; ++i) {
            auto* entry = data + i * size;
            if (*reinterpret_cast<int*>(entry + hash) < 0) continue;
            if (void* object = *reinterpret_cast<void**>(entry + value)) result.push_back(object);
        }
        return result;
    }

    std::unordered_map<int, std::string> enum_names(const char* name_space, const char* name) const {
        std::unordered_map<int, std::string> result;
        void* klass = model_.klass(name_space, name);
        const auto fields = model_.api<void* (*)(void*, void**)>("il2cpp_class_get_fields");
        const auto flags = model_.api<int (*)(void*)>("il2cpp_field_get_flags");
        const auto field_name = model_.api<const char* (*)(void*)>("il2cpp_field_get_name");
        const auto value = model_.api<void (*)(void*, void*)>("il2cpp_field_static_get_value");
        void* iterator = nullptr;
        while (void* field = fields(klass, &iterator)) {
            if (!(flags(field) & 0x0040)) continue;
            std::int32_t number = 0;
            value(field, &number);
            const char* label = field_name(field);
            if (label && *label && !result.count(number)) result.emplace(number, label);
        }
        return result;
    }

    static std::string enum_name(const std::unordered_map<int, std::string>& names, int value) {
        const auto found = names.find(value);
        return found == names.end() ? std::string() : found->second;
    }

    // alliance_chimera_trial_identity and chimera_last_rotation_turn_for_form.
    void load_chimera_static() {
        constexpr const char* enums = "SharedModel.Meta.Alliances.Chimera.Enums";
        chimera_forms_ = enum_names(enums, "ChimeraForm");
        chimera_parts_ = enum_names(enums, "ChimeraChallengePart");
        chimera_levels_ = enum_names(enums, "ChimeraChallengeDifficulty");
        void* alliance = model_.get<void*>(static_data_, "AllianceData");
        void* types = model_.get<void*>(alliance, "ChimeraTypes");
        for (int i = 0; types && i < model_.get<int>(types, "_size"); ++i) {
            void* type = model_.call(types, "get_Item", {&i});
            const int difficulty = model_.get<int>(type, "DifficultyId");
            for (const auto& [id, slot] : dictionary_entries(model_.get<void*>(type, "_challengeTypeById"))) {
                void* challenge = *static_cast<void**>(slot);
                if (!challenge || trial_identities_.count(id)) continue;
                trial_identities_.emplace(id, TrialIdentity{difficulty, model_.get<int>(challenge, "Form"),
                                                            model_.get<int>(challenge, "Part"),
                                                            model_.get<int>(challenge, "Difficulty")});
            }
        }
        for (const auto& [turn, slot] : dictionary_entries(model_.get<void*>(alliance, "ChimeraSequenceForms"))) {
            const int form = *static_cast<int*>(slot);
            last_turn_by_form_[form] = (std::max)(last_turn_by_form_[form], turn);
        }
    }

    TrialIdentity trial_identity(int id) const {
        const auto found = trial_identities_.find(id);
        return found == trial_identities_.end() ? TrialIdentity{} : found->second;
    }

    static std::string decimal(double value) {
        std::ostringstream out;
        out << value;
        return out.str();
    }

    // Same fields, order and derivations as the agent's append_model_challenges.
    void append_challenges(std::ostringstream& out, void* hero) const {
        void* dictionary = has_field(hero, "Challenges") ? model_.get<void*>(hero, "Challenges") : nullptr;
        struct Snapshot {
            int id{};
            void* challenge{};
            TrialIdentity identity{};
            bool completed{};
        };
        std::vector<Snapshot> snapshots;
        for (const auto& [id, slot] : dictionary_entries(dictionary)) {
            void* challenge = *static_cast<void**>(slot);
            if (!challenge) continue;
            snapshots.push_back({id, challenge, trial_identity(id),
                                 model_.unbox<bool>(model_.call(challenge, "get_IsCompleted"))});
        }
        const int boss_turns = model_.get<int>(hero, "TurnCount");
        const int boss_form = model_.get<int>(hero, "CurrentFormIndex");
        out << '[';
        bool first = true;
        for (const auto& snapshot : snapshots) {
            void* c = snapshot.challenge;
            const auto& identity = snapshot.identity;
            const std::int64_t target = model_.get<std::int64_t>(c, "TargetProgress");
            const std::int64_t current = model_.get<std::int64_t>(c, "CurrentProgress");
            std::vector<int> prerequisites, blocking;
            const bool chained = identity.form > 0 && identity.part > 0 && identity.level > 0;
            if (chained) {
                for (const auto& other : snapshots) {
                    if (other.identity.form != identity.form || other.identity.part != identity.part ||
                        other.identity.level <= 0 || other.identity.level >= identity.level)
                        continue;
                    if (prerequisites.size() < 4) prerequisites.push_back(other.id);
                    if (!other.completed && blocking.size() < 4) blocking.push_back(other.id);
                }
            }
            const bool prerequisites_done = blocking.empty();
            const bool unlocked = snapshot.completed || (chained && prerequisites_done);
            const bool active_in_chain = !snapshot.completed && chained && prerequisites_done;
            const bool matching_form = chained && boss_form == identity.form;
            const auto last = last_turn_by_form_.find(identity.form);
            const int last_turn = identity.form > 0 && last != last_turn_by_form_.end() ? last->second : 0;
            const bool feasible = identity.form > 0 && last_turn > 0 && boss_turns >= 0;
            const bool possible = snapshot.completed || !feasible || boss_turns <= last_turn;
            const double ratio = target > 0 ? static_cast<double>(current) / static_cast<double>(target) : 0.0;
            if (!first) out << ',';
            first = false;
            out << "{\"id\":" << snapshot.id
                << ",\"started\":" << boolean(model_.get<bool>(c, "IsStarted"))
                << ",\"completed\":" << boolean(snapshot.completed)
                << ",\"basedOnDamage\":" << boolean(model_.get<bool>(c, "BasedOnDamage"))
                << ",\"targetRaw\":" << target << ",\"currentRaw\":" << current
                << ",\"target\":" << decimal(static_cast<double>(target) / 4294967296.0)
                << ",\"current\":" << decimal(static_cast<double>(current) / 4294967296.0)
                << ",\"progressRatio\":" << decimal(ratio);
            if (identity.form > 0)
                out << ",\"formId\":" << identity.form << ",\"form\":\"" << escaped(enum_name(chimera_forms_, identity.form)) << '"';
            if (identity.part > 0)
                out << ",\"partId\":" << identity.part << ",\"part\":\"" << escaped(enum_name(chimera_parts_, identity.part)) << '"';
            if (identity.level > 0)
                out << ",\"difficultyId\":" << identity.level << ",\"difficulty\":\"" << escaped(enum_name(chimera_levels_, identity.level)) << '"';
            if (chained) {
                out << ",\"requiredPrerequisiteTrialIds\":[";
                for (std::size_t i = 0; i < prerequisites.size(); ++i) out << (i ? "," : "") << prerequisites[i];
                out << "],\"blockingPrerequisiteTrialIds\":[";
                for (std::size_t i = 0; i < blocking.size(); ++i) out << (i ? "," : "") << blocking[i];
                out << "],\"unlocked\":" << boolean(unlocked)
                    << ",\"activeInChain\":" << boolean(active_in_chain)
                    << ",\"matchingCurrentForm\":" << boolean(matching_form)
                    << ",\"eligibleNow\":" << boolean(active_in_chain && matching_form)
                    << ",\"chainState\":\"" << (snapshot.completed ? "completed" : (active_in_chain ? "active" : "locked")) << '"';
            }
            out << ",\"canChangeProgress\":" << boolean(model_.unbox<bool>(model_.call(c, "get_CanChangeProgress")))
                << ",\"canChangeCounter\":" << boolean(model_.unbox<bool>(model_.call(c, "get_CanChangeCounter")))
                << ",\"inProgress\":" << boolean(model_.unbox<bool>(model_.call(c, "get_InProgress")));
            if (feasible) {
                out << ",\"lastEligibleBossTurn\":" << last_turn
                    << ",\"possible\":" << boolean(possible)
                    << ",\"impossible\":" << boolean(!possible);
                if (!possible) out << ",\"impossibilityReason\":\"last_form_window_expired\"";
            }
            auto nullable = [&](const char* field, const char* label) {
                const auto raw = model_.get<std::array<unsigned char, 8>>(c, field);
                if (!raw[0]) return;
                std::int32_t number = 0;
                std::memcpy(&number, raw.data() + 4, 4);
                out << ",\"" << label << "\":" << number;
            };
            nullable("CounterLimit", "counterLimit");
            nullable("CurrentCounter", "currentCounter");
            nullable("SelfTurnWhichCompleted", "selfTurnWhichCompleted");
            out << '}';
        }
        out << ']';
    }

    void load_effect_kind_names() {
        void* klass = model_.klass("SharedModel.Battle.Effects", "EffectKindId");
        const auto fields = model_.api<void* (*)(void*, void**)>("il2cpp_class_get_fields");
        const auto flags = model_.api<int (*)(void*)>("il2cpp_field_get_flags");
        const auto name = model_.api<const char* (*)(void*)>("il2cpp_field_get_name");
        const auto value = model_.api<void (*)(void*, void*)>("il2cpp_field_static_get_value");
        constexpr int literal = 0x0040;
        void* iterator = nullptr;
        int count = 0;
        while (void* field = fields(klass, &iterator)) {
            if (++count > 4096) throw std::runtime_error("EffectKindId enum exceeds bound");
            if (!(flags(field) & literal)) continue;
            std::int32_t number = 0;
            value(field, &number);
            const char* label = name(field);
            // Keep the first declared name for aliased values, like the agent.
            if (label && *label && !effect_kind_names_.count(number))
                effect_kind_names_.emplace(number, label);
        }
        if (effect_kind_names_.empty()) throw std::runtime_error("EffectKindId enum has no members");
    }

    std::string avatar(int type_id, int form_index) {
        const auto key = std::make_pair(type_id, form_index);
        const auto found = avatars_.find(key);
        if (found != avatars_.end()) return found->second;
        std::string result;
        try {
            void* hero_type = model_.call(hero_types_, "get_Item", {&type_id});
            void* method = hero_type ? find_method(hero_type, "AvatarUrl", 2) : nullptr;
            if (method) {
                // A zeroed Nullable<int> is "no skin", matching the live agent.
                std::uint64_t no_skin = 0;
                int form = form_index;
                result = model_.string_value(model_.invoke(method, hero_type, {&no_skin, &form}));
            }
        } catch (const std::exception&) {
            result.clear();
        }
        avatars_.emplace(key, result);
        return result;
    }

    void* hero_skill_list(void* hero) const {
        // append_model_skills prefers the private hero-skill list.
        void* skills = has_field(hero, "_heroSkills") ? model_.get<void*>(hero, "_heroSkills") : nullptr;
        if (!skills || model_.get<int>(skills, "_size") == 0) skills = model_.get<void*>(hero, "Skills");
        if (!skills) throw std::runtime_error("Policy projection hero has no skill list");
        const int count = model_.get<int>(skills, "_size");
        if (count < 0 || count > 64) throw std::runtime_error("Policy projection skill count exceeds bound");
        return skills;
    }

    void append_model_skills(std::ostringstream& out, void* hero) const {
        void* skills = hero_skill_list(hero);
        out << '[';
        bool first = true;
        for (int i = 0; i < model_.get<int>(skills, "_size"); ++i) {
            void* skill = model_.call(skills, "get_Item", {&i});
            if (!skill || !model_.get<bool>(skill, "<IsHeroSkill>k__BackingField")) continue;
            const bool disabled = model_.get<bool>(skill, "<Disabled>k__BackingField");
            const bool active = model_.unbox<bool>(model_.call(skill, "get_IsActive"));
            const bool hidden = model_.unbox<bool>(model_.call(skill, "IsHiddenOnHud"));
            const int cooldown = model_.get<int>(skill, "Cooldown");
            if (!first) out << ',';
            first = false;
            out << "{\"typeId\":" << model_.get<int>(skill, "TypeId")
                << ",\"level\":" << model_.get<int>(skill, "Level")
                << ",\"cooldown\":" << cooldown
                << ",\"maxCooldown\":" << model_.get<int>(skill, "MaxCooldown")
                << ",\"ready\":" << boolean(!disabled && active && !hidden && cooldown == 0)
                << ",\"disabled\":" << boolean(disabled)
                << ",\"heroSkill\":true"
                << ",\"activeSkill\":" << boolean(active)
                << ",\"hiddenOnHud\":" << boolean(hidden) << '}';
        }
        out << ']';
    }

    void append_effects(std::ostringstream& out, void* hero_state) const {
        void* effects = model_.get<void*>(hero_state, "AppliedEffects");
        if (!effects) throw std::runtime_error("Policy projection hero has no effect list");
        const int count = model_.get<int>(effects, "_size");
        if (count < 0 || count > 256) throw std::runtime_error("Policy projection effect count exceeds bound");
        out << '[';
        for (int i = 0; i < count; ++i) {
            if (i) out << ',';
            void* effect = model_.call(effects, "get_Item", {&i});
            void* type = model_.get<void*>(effect, "_type");
            const int kind = type ? model_.get<int>(type, "KindId") : 0;
            const int group = type ? model_.get<int>(type, "Group") : 0;
            const auto name = effect_kind_names_.find(kind);
            out << "{\"id\":" << model_.get<int>(effect, "Id")
                << ",\"producerId\":" << model_.get<int>(effect, "ProducerId")
                << ",\"skillTypeId\":" << model_.get<int>(effect, "SkillTypeId")
                << ",\"effectTypeId\":" << model_.get<int>(effect, "EffectTypeId")
                << ",\"effectKindId\":" << kind
                << ",\"effectKind\":\"" << (name == effect_kind_names_.end() ? "" : escaped(name->second)) << '"'
                << ",\"effectGroupId\":" << group
                << ",\"applyTurn\":" << model_.get<int>(effect, "ApplyTurn")
                << ",\"lifetime\":" << model_.get<int>(effect, "Lifetime")
                << ",\"turnsLeft\":" << model_.get<int>(effect, "TurnLeft");
            void* digestion = model_.get<void*>(effect, "DigestionInfo");
            if (digestion)
                out << ",\"devouredHeroId\":" << model_.get<int>(digestion, "DevouredHeroId");
            out << '}';
        }
        out << ']';
    }

    // read_hydra_digestion_state: first effect with a non-negative devoured hero.
    void digestion_state(void* hero_state, int& devoured, int& turns) const {
        void* effects = model_.get<void*>(hero_state, "AppliedEffects");
        for (int i = 0; effects && i < model_.get<int>(effects, "_size"); ++i) {
            void* effect = model_.call(effects, "get_Item", {&i});
            void* digestion = model_.get<void*>(effect, "DigestionInfo");
            if (!digestion) continue;
            const int hero_id = model_.get<int>(digestion, "DevouredHeroId");
            if (hero_id < 0) continue;
            devoured = hero_id;
            turns = model_.get<int>(effect, "TurnLeft");
            return;
        }
    }

    void append_team(std::ostringstream& out, void* state, const char* team_name,
                     const char* side, bool living_only) {
        void* team = model_.get<void*>(state, team_name);
        void* heroes = team ? model_.get<void*>(team, "Heroes") : nullptr;
        if (!heroes) throw std::runtime_error("Policy projection has no team heroes");
        const int count = model_.get<int>(heroes, "_size");
        if (count < 0 || count > 4096) throw std::runtime_error("Policy projection team size exceeds bound");
        out << '[';
        bool first = true;
        for (int i = 0; i < count; ++i) {
            void* hero = model_.call(heroes, "get_Item", {&i});
            if (!hero) throw std::runtime_error("Policy projection has a null hero");
            void* hero_state = model_.get<void*>(hero, "_heroState");
            if (!hero_state) throw std::runtime_error("Policy projection has no hero state");
            const bool dead = model_.get<bool>(hero_state, "IsDead");
            if (living_only && dead) continue;
            const int type_id = model_.get<int>(hero, "TypeId");
            const int form = model_.get<int>(hero, "CurrentFormIndex");
            const std::int64_t health = model_.get<std::int64_t>(hero, "Health");
            const std::int64_t maximum = model_.unbox<std::int64_t>(model_.call(hero, "get_MaxHealth"));
            bool hydra_head = false, hydra_neck = false, digesting = false;
            optional_bool_method(hero, "get_IsHydraHead", hydra_head);
            optional_bool_method(hero, "get_IsHydraNeck", hydra_neck);
            optional_bool_method(hero, "get_IsDigestingNow", digesting);
            int devoured = -1, digestion_turns = -1;
            digestion_state(hero_state, devoured, digestion_turns);
            const double percent = maximum > 0
                ? static_cast<double>(health) * 100.0 / static_cast<double>(maximum) : 0.0;
            if (!first) out << ',';
            first = false;
            // Default stream precision deliberately matches the agent's output.
            out << "{\"id\":" << actor_id(hero)
                << ",\"typeId\":" << type_id
                << ",\"inventoryHeroId\":" << model_.get<int>(hero, "InventoryHeroId")
                << ",\"avatar\":\"" << escaped(avatar(type_id, form)) << '"'
                << ",\"side\":\"" << side << "\",\"modelFound\":true"
                << ",\"currentFormIndex\":" << form
                << ",\"turnCount\":" << model_.get<int>(hero, "TurnCount")
                << ",\"healthRaw\":" << health << ",\"maxHealthRaw\":" << maximum
                << ",\"healthPct\":" << percent
                << ",\"dead\":" << boolean(dead)
                << ",\"active\":" << boolean(model_.unbox<bool>(model_.call(hero_state, "get_IsActive")))
                << ",\"isHydraHead\":" << boolean(hydra_head)
                << ",\"isHydraNeck\":" << boolean(hydra_neck)
                << ",\"headState\":\"" << (hydra_neck ? "exposed_neck" : (hydra_head ? "head" : "")) << '"'
                << ",\"devouredHeroId\":" << devoured
                << ",\"isDevouring\":" << boolean(digesting || devoured >= 0)
                << ",\"digestionTurns\":" << digestion_turns
                << ",\"battlePosition\":" << model_.get<int>(hero, "SlotId")
                << ",\"states\":{\"stunned\":" << boolean(model_.get<bool>(hero_state, "IsStunned"))
                << ",\"frozen\":" << boolean(model_.get<bool>(hero_state, "IsFrozen"))
                << ",\"sleep\":" << boolean(model_.get<bool>(hero_state, "IsSleep"))
                << ",\"provoked\":" << boolean(model_.get<bool>(hero_state, "IsProvoked"))
                << ",\"activeSkillsBlocked\":" << boolean(model_.get<bool>(hero_state, "ActiveSkillsBlocked"))
                << ",\"duelProducer\":" << boolean(model_.get<bool>(hero_state, "IsDuelProducer"))
                << ",\"duelTarget\":" << boolean(model_.get<bool>(hero_state, "IsDuelTarget"))
                << ",\"enfeeble\":" << boolean(model_.get<bool>(hero_state, "IsEnfeeble"))
                << ",\"rages\":" << boolean(model_.get<bool>(hero_state, "IsRages"))
                << "},\"skills\":";
            append_model_skills(out, hero);
            out << ",\"effects\":";
            append_effects(out, hero_state);
            out << ",\"challenges\":";
            if (chimera_) append_challenges(out, hero);
            else out << "[]";
            out << '}';
        }
        out << ']';
    }

    // HUD skill catalog: slots are 1 + index in the hero-skill list (hidden
    // skills keep their index but are not listed), as observed live.
    void append_hud_skills(std::ostringstream& out, void* state, void* active) const {
        void* skills = hero_skill_list(active);
        // The private cache is cleared when heads change; the original
        // accessor rebuilds it without touching RNG or battle state.
        void* all_heroes = model_.call(state, "EnumerateHeroes");
        if (!all_heroes) throw std::runtime_error("Policy projection cannot enumerate targets");
        void* random = model_.get<void*>(state, "Random");
        out << '[';
        bool first = true;
        int index = 0;
        for (int i = 0; i < model_.get<int>(skills, "_size"); ++i) {
            void* skill = model_.call(skills, "get_Item", {&i});
            if (!skill || !model_.get<bool>(skill, "<IsHeroSkill>k__BackingField")) continue;
            const int skill_id = index++;
            if (model_.unbox<bool>(model_.call(skill, "IsHiddenOnHud"))) continue;
            const int type_id = model_.get<int>(skill, "TypeId");
            const bool passive = !model_.unbox<bool>(model_.call(skill, "get_IsActive"));
            const bool blocked = model_.get<bool>(skill, "<Disabled>k__BackingField") ||
                                 model_.unbox<bool>(model_.call(skill, "get_Blocked"));
            const int cooldown = model_.get<int>(skill, "Cooldown");
            void* static_skill = model_.call(skill_types_, "get_Item", {const_cast<int*>(&type_id)});
            if (!static_skill) throw std::runtime_error("Active hero skill has no static type");
            std::ostringstream targets;
            int target_count = 0;
            targets << '[';
            if (!passive && !blocked && cooldown == 0) {
                const auto raw = model_.get<std::array<unsigned char, 8>>(static_skill, "Targets");
                if (raw[0] != 1 || raw[1] || raw[2] || raw[3])
                    throw std::runtime_error("Active skill has no explicit target kind");
                int target_kind = static_cast<int>(raw[4]) | (static_cast<int>(raw[5]) << 8) |
                                  (static_cast<int>(raw[6]) << 16) | (static_cast<int>(raw[7]) << 24);
                const auto before = offline_rng_words(model_, random);
                void* candidates = model_.invoke(selector_, nullptr,
                                                 {all_heroes, active, &target_kind, static_skill});
                if (offline_rng_words(model_, random) != before)
                    throw std::runtime_error("Target selection advanced the original RNG");
                if (!candidates) throw std::runtime_error("Original target selector returned null");
                target_count = model_.get<int>(candidates, "_size");
                if (target_count < 0 || target_count > 4096)
                    throw std::runtime_error("Original target count exceeds bound");
                for (int j = 0; j < target_count; ++j) {
                    if (j) targets << ',';
                    targets << actor_id(model_.call(candidates, "get_Item", {&j}));
                }
            }
            targets << ']';
            if (!first) out << ',';
            first = false;
            out << "{\"slot\":" << skill_id + 1 << ",\"skillId\":" << skill_id
                << ",\"typeId\":" << type_id
                << ",\"cooldown\":" << cooldown
                << ",\"defaultCooldown\":" << model_.get<int>(static_skill, "Cooldown")
                << ",\"passive\":" << boolean(passive)
                << ",\"blocked\":" << boolean(blocked)
                << ",\"ready\":" << boolean(!passive && !blocked && cooldown == 0 && target_count > 0)
                << ",\"validTargetIds\":" << targets.str() << '}';
        }
        out << ']';
    }
};
