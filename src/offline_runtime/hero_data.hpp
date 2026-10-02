#pragma once

#include "static_dump.hpp"
#include <functional>
#include <initializer_list>
#include <set>
#include <string>
#include <vector>

// "hero-data": the champions' static definitions (StaticHeroData.HeroTypes)
// with their skills, read from the copied static-data cache, for the desktop
// app's champion search and profiles. parameter is "all" (the playable base
// types and their ascension variants, base id + 1..6) or a comma-separated
// list of hero type ids (exactly those). Each skill is written in full once;
// later heroes that share it list only its id. The localized hero and skill
// names and descriptions are added under "texts", the client's labels for
// factions, affinities, roles, rarities, stats and status effects under
// "labels", and the status effect types (EffectData.EffectTypes) under
// "effectTypes".
inline std::string export_hero_data(const ManagedRuntime& model, void* static_data, const std::wstring& parameter,
                                    const std::function<void(const char*)>& progress) {
    std::set<int> wanted;
    const bool all = parameter == L"all";
    if (!all) {
        std::size_t start = 0;
        while (start <= parameter.size()) {
            const auto comma = parameter.find(L',', start);
            const auto item = parameter.substr(start, comma == std::wstring::npos ? std::wstring::npos : comma - start);
            if (!item.empty()) wanted.insert(std::stoi(item));
            if (comma == std::wstring::npos) break;
            start = comma + 1;
        }
    }
    ReflectiveJson dump(model, 12);
    const auto header = static_cast<std::size_t>(model.api<std::uintptr_t (*)()>("il2cpp_array_object_header_size")());
    auto list_items = [&](void* list) {
        std::vector<void*> result;
        if (!list) return result;
        void* items = model.get<void*>(list, "_items");
        const int size = model.get<int>(list, "_size");
        for (int index = 0; items && index < size; ++index)
            result.push_back(*reinterpret_cast<void**>(static_cast<unsigned char*>(items) + header + index * sizeof(void*)));
        return result;
    };
    auto flag = [&](void* hero, const char* getter, bool fallback) {
        try {
            void* boxed = model.call(hero, getter);
            return boxed ? model.unbox<bool>(boxed) : fallback;
        } catch (const std::exception&) {
            return fallback;
        }
    };

    progress("hero_types");
    void* section = model.get<void*>(static_data, "HeroData");
    const auto heroes = list_items(model.get<void*>(section, "HeroTypes"));
    // Playable base types first, then their ascension variants.
    std::set<int> bases;
    if (all) {
        for (void* hero : heroes) {
            if (!hero) continue;
            const auto mark = model.root_mark();
            if (flag(hero, "get_IsBaseType", true) && !flag(hero, "get_IsBoss", false)
                && flag(hero, "get_IsAvailableToUser", true))
                bases.insert(model.get<int>(hero, "Id"));
            model.release_roots_since(mark);
        }
    }
    std::set<int> skills_written;
    std::string out = "\"heroData\":{\"heroes\":[";
    bool first = true;
    int written = 0;
    for (void* hero : heroes) {
        if (!hero) continue;
        const int id = model.get<int>(hero, "Id");
        const int variant = id % 10;
        if (all ? !(bases.count(id) || (variant >= 1 && variant <= 6 && bases.count(id - variant))) : !wanted.count(id))
            continue;
        const auto mark = model.root_mark();
        if (!first) out += ',';
        first = false;
        out += "{\"id\":" + std::to_string(id) + ",\"hero\":" + dump.json(hero) + ",\"skills\":[";
        try {
            bool first_skill = true;
            for (void* skill : list_items(model.call(hero, "get_AllSkillTypes"))) {
                if (!skill) continue;
                if (!first_skill) out += ',';
                first_skill = false;
                const int skill_id = model.get<int>(skill, "Id");
                out += skills_written.insert(skill_id).second ? dump.json(skill) : std::to_string(skill_id);
            }
            out += ']';
        } catch (const std::exception& error) {
            out += "],\"skillError\":" + ReflectiveJson::quoted(error.what());
        }
        out += '}';
        model.release_roots_since(mark);
        if (++written % 200 == 0) progress("hero_types");
    }
    out += ']';

    // Localized texts in the client's language. StaticDataLocalization maps keys
    // such as "l10n:skill/description?id=105401#static"; ClientLocalization
    // holds the client's own labels (factions, affinities, roles, stats).
    auto texts = [&](const char* dictionary, std::initializer_list<const char*> prefixes) {
        std::string json = "{";
        try {
            void* texts = model.get<void*>(static_data, dictionary);
            void* entries = texts ? model.get<void*>(texts, "_entries") : nullptr;
            const int count = texts ? model.get<int>(texts, "_count") : 0;
            if (!entries || count <= 0) return json + '}';
            auto object_class = model.api<void* (*)(void*)>("il2cpp_object_get_class");
            auto element_class = model.api<void* (*)(void*)>("il2cpp_class_get_element_class");
            auto element_size = model.api<int (*)(void*)>("il2cpp_class_array_element_size");
            auto field_from_name = model.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name");
            auto field_offset = model.api<std::size_t (*)(void*)>("il2cpp_field_get_offset");
            void* entry = element_class(object_class(entries));
            const auto size = static_cast<std::size_t>(element_size(entry));
            void* hash_field = field_from_name(entry, "hashCode");
            void* key_field = field_from_name(entry, "key");
            void* value_field = field_from_name(entry, "value");
            const auto* data = static_cast<const unsigned char*>(entries) + header;
            constexpr std::size_t kHeader = 16;
            bool first_text = true;
            for (int index = 0; hash_field && key_field && value_field && index < count; ++index) {
                const unsigned char* item = data + index * size;
                if (*reinterpret_cast<const std::int32_t*>(item + field_offset(hash_field) - kHeader) < 0) continue;
                void* key = *reinterpret_cast<void* const*>(item + field_offset(key_field) - kHeader);
                void* value = *reinterpret_cast<void* const*>(item + field_offset(value_field) - kHeader);
                const std::string name = model.string_value(key);
                bool matches = false;
                for (const char* prefix : prefixes) matches = matches || name.rfind(prefix, 0) == 0;
                if (!matches) continue;
                if (!first_text) json += ',';
                first_text = false;
                json += ReflectiveJson::quoted(name) + ':' + dump.json(value);
            }
        } catch (const std::exception& error) {
            if (json.size() > 1) json += ',';
            json += "\"error\":" + ReflectiveJson::quoted(error.what());
        }
        return json + '}';
    };
    progress("texts");
    out += ",\"texts\":" + texts("StaticDataLocalization", {"l10n:hero-type/name?", "l10n:skill/name?",
                                                            "l10n:skill/description?", "l10n:area/name?"});
    out += ",\"labels\":" + texts("ClientLocalization", {"l10n:hero/fraction?name=", "l10n:hero/element/",
                                                         "l10n:hero/role/", "l10n:hero/rarity/", "l10n:leader-skill/",
                                                         "l10n:common/hero-stats/short/", "l10n:battle-hud/effect-kindId?id=",
                                                         "l10n:status-effects/effect-kindId?id="});
    // Status effects (buffs and debuffs) by type id: kind and strength.
    progress("effect_types");
    try {
        out += ",\"effectTypes\":" + dump.json(model.get<void*>(model.get<void*>(static_data, "EffectData"), "EffectTypes"));
    } catch (const std::exception& error) {
        out += ",\"effectTypes\":" + ReflectiveJson::quoted(std::string("error: ") + error.what());
    }
    out += ",\"enums\":" + dump.enums() + ",\"truncated\":" + std::to_string(dump.truncated) + '}';
    return out;
}
