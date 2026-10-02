"""Backend message tokens: encoding, the formatter port, and the messages the backend uses."""
import ast
import json
from pathlib import Path

import ui_text
from ui_text import has_tokens, message_key, render, tokens

ROOT = Path(__file__).resolve().parents[1]


def test_formatter_matches_the_window() -> None:
    # The same cases ui/tests/catalog.test.cjs runs against format.ts.
    for locale, pattern, params, expected in json.loads((ROOT / "ui" / "tests" / "format-cases.json").read_text(encoding="utf-8")):
        assert ui_text.format_message(locale, pattern, params) == expected, (locale, pattern, params)


def test_tokens_survive_joins_and_nesting() -> None:
    inner = ui_text.ui_text("mode.hydra")
    outer = ui_text.ui_text("app.runLog", bossMode=inner)
    line = f"12:00 · {outer} · {ui_text.ui_text('mode.chimera')}"
    assert [key for key, _ in tokens(line)] == ["app.runLog", "mode.chimera"]
    assert render(line, "en") == "12:00 · Hydra run log · Chimera"
    assert render(line, "zh-CN") == "12:00 · 六头蛇运行记录 · 奇美拉"
    assert render(line, "pt-BR") == "12:00 · Registro de execução: Hidra · Quimera"
    assert has_tokens(line) and not has_tokens("普通文本")
    assert message_key(outer) == "app.runLog" and message_key(line) is None
    # Text from users and the game cannot close a token early.
    tricky = ui_text.ui_text("app.rule", value="a⸩b⸨c")
    assert render(tricky, "en") == "Rule a⸩b⸨c"


def test_old_records_and_unknown_keys_pass_through() -> None:
    assert render("控制器已启动。", "en") == "控制器已启动。"
    assert render(ui_text.ui_text("no.such.key"), "en") == "no.such.key"


def _calls() -> list[tuple[str, int, str, set[str]]]:
    found = []
    for path in sorted((ROOT / "tools").glob("*.py")):
        if path.name.startswith("test_") or path.name == "ui_text.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ui_text"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.append((path.name, node.lineno, node.args[0].value, {keyword.arg for keyword in node.keywords}))
    return found


def test_every_backend_message_exists_with_its_arguments() -> None:
    calls = _calls()
    problems = []
    for locale in ui_text.LOCALES:
        catalog = ui_text.catalog(locale)
        for file, line, key, names in calls:
            if key not in catalog:
                problems.append(f"{file}:{line} {key} missing in {locale}")
            elif None not in names and set(ui_text.message_arguments(catalog[key])) != names:  # None: **arguments
                problems.append(f"{file}:{line} {key} arguments {sorted(names)} != {ui_text.message_arguments(catalog[key])}")
    assert not problems, "\n".join(problems[:40])
