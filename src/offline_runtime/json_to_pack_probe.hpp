#pragma once

#include "managed_runtime.hpp"
#include "static_data_probe.hpp"

#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

struct OriginalJsonSetupPack {
    std::vector<unsigned char> packed;
    std::array<unsigned char, 16> guid_bytes{};
    int seed{};
    int stage_id{};
    std::int64_t team_owner_id{};
    int hero_slot_base{-1};
    std::vector<int> hero_instance_ids;
};

struct OriginalJsonSettingsPack {
    std::vector<unsigned char> packed;
    int active_engine_version{};
    int warmup_battle_random_count{};
    int max_turns_in_battle{};
};

// The input is a previously copied, bounded local JSON BattleSetup, never a
// live game object. Deserialize and pack with the same-version original code
// inside the no-network AppContainer. The caller must independently bind the
// returned identity to the captured battle before using the packed output.
inline OriginalJsonSetupPack original_json_setup_to_pack(
    const ManagedRuntime& model, void* domain, const std::filesystem::path& input_path) {
    if (!std::filesystem::is_regular_file(input_path) ||
        std::filesystem::file_size(input_path) == 0 ||
        std::filesystem::file_size(input_path) > 32 * 1024 * 1024)
        throw std::runtime_error("BattleSetup JSON input is absent or exceeds 32 MiB");
    std::ifstream stream(input_path, std::ios::binary);
    std::string json((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    if (!stream.eof() && stream.fail()) throw std::runtime_error("Cannot read BattleSetup JSON input");
    const auto first = json.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) throw std::runtime_error("BattleSetup JSON input is empty");
    if (json[first] == '{') json = "[" + json + "]";
    else if (json[first] != '[') throw std::runtime_error("BattleSetup JSON must be an object or list");

    ManagedRuntime client(model.library, domain, "Unity.Model.dll");
    void* cache = client.klass("Client.Model.Gameplay.Battle", "BattleSetupsCache");
    auto field = client.api<void* (*)(void*, const char*)>("il2cpp_class_get_field_from_name")(
        cache, "_cachedSetups");
    if (!field) throw std::runtime_error("Original BattleSetup list type unavailable");
    auto list_type = client.api<void* (*)(void*)>("il2cpp_field_get_type")(field);
    auto list_class = client.api<void* (*)(void*)>("il2cpp_class_from_type")(list_type);
    if (!list_class) throw std::runtime_error("Original BattleSetup list class unavailable");

    ManagedRuntime common(model.library, domain, "Unity.Plarium.Common.dll");
    auto input = common.keep(common.api<void* (*)(const char*, std::uint32_t)>(
        "il2cpp_string_new_len")(json.data(), static_cast<std::uint32_t>(json.size())));
    auto json_main = common.klass("Plarium.Common.Serialization", "JsonMain");
    auto parse = common.method_exact(json_main, "FromJsonStr", {"System.String", "System.Type"});
    void* type = common.type_object(list_class);
    void* list = model.keep(common.invoke(parse, nullptr, {input, type}));
    if (!list || model.get<int>(list, "_size") != 1)
        throw std::runtime_error("Original JSON parser did not return exactly one BattleSetup");
    int index = 0;
    void* setup = model.call(list, "get_Item", {&index});
    if (!setup || model.object_class(setup) != model.klass("SharedModel.Battle.Core.Setup", "BattleSetup"))
        throw std::runtime_error("Original JSON parser did not return a BattleSetup");
    // Hydra (KindId 5) brings six heroes, Chimera (KindId 8) five.
    const int kind = model.get<int>(setup, "KindId");
    if (kind != 5 && kind != 8)
        throw std::runtime_error("Original JSON parser did not return a Hydra or Chimera BattleSetup");
    const int team_size = kind == 5 ? 6 : 5;
    auto read_identity = [&](void* value) {
        OriginalJsonSetupPack result{};
        result.guid_bytes = model.get<std::array<unsigned char, 16>>(value, "Id");
        result.seed = model.get<int>(value, "RandomSeed");
        result.stage_id = model.get<int>(value, "StageId");
        void* team = model.get<void*>(value, "FirstTeam");
        if (!team) throw std::runtime_error("Parsed setup has no FirstTeam");
        result.team_owner_id = model.get<std::int64_t>(team, "TeamOwnerId");
        void* heroes = model.get<void*>(team, "HeroSlotSetups");
        if (!heroes || model.get<int>(heroes, "_size") != team_size)
            throw std::runtime_error("Parsed setup has an unexpected number of player hero slots");
        for (int i = 0; i < team_size; ++i) {
            void* hero = model.call(heroes, "get_Item", {&i});
            if (!hero)
                throw std::runtime_error("Parsed Hydra hero slot order is invalid");
            const int slot = model.get<int>(hero, "Slot");
            if (i == 0) {
                if (slot != 0 && slot != 1)
                    throw std::runtime_error("Parsed Hydra hero slot order is invalid");
                result.hero_slot_base = slot;
            }
            if (slot != i + result.hero_slot_base)
                throw std::runtime_error("Parsed Hydra hero slot order is invalid");
            result.hero_instance_ids.push_back(model.get<int>(hero, "InventoryHeroId"));
        }
        return result;
    };
    auto result = read_identity(setup);
    std::size_t packed_size = 0;
    void* restored = roundtrip_packed_messagepack(model, domain, setup, &packed_size, &result.packed);
    if (packed_size > 1024 * 1024 || result.packed.size() != packed_size)
        throw std::runtime_error("Original packed BattleSetup exceeds 1 MiB");
    const auto after = read_identity(restored);
    if (after.guid_bytes != result.guid_bytes || after.seed != result.seed ||
        after.stage_id != result.stage_id || after.team_owner_id != result.team_owner_id ||
        after.hero_slot_base != result.hero_slot_base ||
        after.hero_instance_ids != result.hero_instance_ids)
        throw std::runtime_error("Original BattleSetup MessagePack round trip changed battle identity");
    return result;
}

inline OriginalJsonSettingsPack original_json_settings_to_pack(
    const ManagedRuntime& model, void* domain, const std::filesystem::path& input_path) {
    if (!std::filesystem::is_regular_file(input_path) ||
        std::filesystem::file_size(input_path) == 0 ||
        std::filesystem::file_size(input_path) > 1024 * 1024)
        throw std::runtime_error("BattleSettings JSON input is absent or exceeds 1 MiB");
    std::ifstream stream(input_path, std::ios::binary);
    std::string json((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    if (!stream.eof() && stream.fail())
        throw std::runtime_error("Cannot read BattleSettings JSON input");
    const auto first = json.find_first_not_of(" \t\r\n");
    if (first == std::string::npos || json[first] != '{')
        throw std::runtime_error("BattleSettings JSON must be an object");

    ManagedRuntime common(model.library, domain, "Unity.Plarium.Common.dll");
    void* input = common.keep(common.api<void* (*)(const char*, std::uint32_t)>(
        "il2cpp_string_new_len")(json.data(), static_cast<std::uint32_t>(json.size())));
    void* json_main = common.klass("Plarium.Common.Serialization", "JsonMain");
    void* parse = common.method_exact(json_main, "FromJsonStr",
                                      {"System.String", "System.Type"});
    void* klass = model.klass("SharedModel.Meta.Battle", "BattleSettings");
    void* type = common.type_object(klass);
    void* settings = model.keep(common.invoke(parse, nullptr, {input, type}));
    if (!settings || model.object_class(settings) != klass)
        throw std::runtime_error("Original JSON parser did not return BattleSettings");
    auto read_identity = [&](void* value) {
        OriginalJsonSettingsPack result{};
        result.active_engine_version = model.get<int>(value, "ActiveEngineVersion");
        result.warmup_battle_random_count = model.get<int>(value, "WarmupBattleRandomCount");
        result.max_turns_in_battle = model.get<int>(value, "MaxTurnsInBattle");
        return result;
    };
    auto result = read_identity(settings);
    std::size_t packed_size = 0;
    void* restored = roundtrip_packed_messagepack(model, domain, settings,
                                                   &packed_size, &result.packed);
    if (packed_size > 1024 * 1024 || result.packed.size() != packed_size)
        throw std::runtime_error("Original packed BattleSettings exceeds 1 MiB");
    const auto after = read_identity(restored);
    if (after.active_engine_version != result.active_engine_version ||
        after.warmup_battle_random_count != result.warmup_battle_random_count ||
        after.max_turns_in_battle != result.max_turns_in_battle)
        throw std::runtime_error("Original BattleSettings MessagePack round trip changed critical fields");
    return result;
}
