#!/usr/bin/env python3
from __future__ import annotations

from chimera_icons import EFFECT_OPTIONS, EFFECT_TYPE_ICONS, runtime_effect_options


def main() -> int:
    options_by_token = {option["token"]: option for option in EFFECT_OPTIONS}
    assert options_by_token["350"]["label"] == "虚弱 25%"
    assert options_by_token["350"]["icon"] == "IncreaseDamageTaken2"
    assert options_by_token["351"]["label"] == "虚弱 15%"
    assert options_by_token["351"]["icon"] == "IncreaseDamageTaken"
    assert EFFECT_TYPE_ICONS[350] == "IncreaseDamageTaken2"
    assert EFFECT_TYPE_ICONS[351] == "IncreaseDamageTaken"
    runtime = [
        {"id": 880, "name": "MagmaShield"},
        {"id": 560, "name": "BlockPassiveSkills"},
        {"id": 99999, "name": "FutureGameEffect"},
    ]
    options = runtime_effect_options(runtime)
    by_token = {option["token"]: option for option in options}

    assert len(by_token) == len(options), "effect tokens must remain unique"
    assert len(options) == len(EFFECT_OPTIONS) + 3
    assert by_token["880"]["token"] == "880"
    assert by_token["880"]["icon"] == "MagmaShield"
    assert by_token["880"]["label"] == "熔岩护盾"
    assert by_token["880"]["labelEn"] == "Magma Shield"
    assert by_token["880"]["nativeName"] == "MagmaShield"
    assert by_token["880"]["group"] == "增益"
    assert isinstance(by_token["880"]["iconReady"], bool)
    assert by_token["560"]["icon"] == "BlockPassiveSkills"
    assert by_token["560"]["group"] == "减益"
    assert by_token["99999"]["icon"] == "Status_Effect_Temp"
    assert by_token["99999"]["label"] == "Future Game Effect"
    assert by_token["99999"]["labelEn"] == "Future Game Effect"
    print("effect catalog tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
