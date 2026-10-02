"""Text the backend shows in the window, in the window's language.

The backend does not write sentences for the window. It writes message tokens,
⸨["key", {params}]⸩, inside ordinary strings: log lines, status, errors and
notices. The window replaces each token with the message from the catalogs in
ui/src/i18n/messages in its own language; text outside tokens (game names,
numbers, separators) stays as it is. A string param can hold tokens itself.
Strings without tokens are records written before 1.1.2 (Chinese).

render() does the same here, for diagnostics, files and tests, with a port of
the window's message formatter (ui/src/i18n/format.ts).
"""
from __future__ import annotations

import json
import math
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

OPEN, CLOSE = "⸨", "⸩"
FALLBACK = "en"
_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
CATALOG_DIR = _ROOT / "ui" / "src" / "i18n" / "messages"
# The offered languages are the catalog files (the window lists the same ones in locales.ts).
LOCALES = tuple(sorted((path.stem for path in CATALOG_DIR.glob("*.json")), key=lambda code: (code != FALLBACK, code))) or (FALLBACK,)


def _param(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def ui_text(key: str, /, **params: Any) -> str:
    """A message token for the window: ui_text("log.started", account="Name")."""
    body = [key, {name: _param(value) for name, value in params.items()}] if params else [key]
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    # Tokens inside params stay inside: their brackets are escaped in the JSON.
    return OPEN + payload.replace(OPEN, "\\u2e28").replace(CLOSE, "\\u2e29") + CLOSE


def tokens(text: Any) -> list[tuple[str, dict[str, Any]]]:
    """The messages in a string, in order."""
    found = []
    for match in _TOKEN.finditer(str(text or "")):
        decoded = _decode(match.group(1))
        if decoded:
            found.append(decoded)
    return found


def has_tokens(text: Any) -> bool:
    return isinstance(text, str) and OPEN in text and bool(tokens(text))


def message_key(text: Any) -> str | None:
    """The key of a string that is one message token, else None."""
    if not isinstance(text, str):
        return None
    match = _TOKEN.fullmatch(text.strip())
    decoded = _decode(match.group(1)) if match else None
    return decoded[0] if decoded else None


_TOKEN = re.compile(re.escape(OPEN) + r"(.*?)" + re.escape(CLOSE), re.S)


def _decode(payload: str) -> tuple[str, dict[str, Any]] | None:
    try:
        value = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(value, list) or not value or not isinstance(value[0], str):
        return None
    params = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
    return value[0], params


def match_locale(tag: Any) -> str:
    """The offered language closest to a language tag (ui/src/i18n/locales.ts matchLocale)."""
    text = str(tag or "").replace("_", "-").lower()
    for locale in LOCALES:
        if text == locale.lower():
            return locale
    base = text.split("-")[0]
    return next((locale for locale in LOCALES if locale.lower().split("-")[0] == base), FALLBACK) if base else FALLBACK


def system_locale() -> str:
    """The offered language closest to Windows' display language."""
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultUILanguage() and ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, 85):
            return match_locale(buffer.value)
    except (AttributeError, OSError):
        pass
    return FALLBACK


@lru_cache(maxsize=None)
def catalog(locale: str) -> dict[str, str]:
    try:
        return json.loads((CATALOG_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def render(text: Any, locale: str = FALLBACK) -> str:
    """A string with its tokens replaced by messages in the language."""
    value = "" if text is None else str(text)
    if OPEN not in value:
        return value

    def replace(match: re.Match[str]) -> str:
        decoded = _decode(match.group(1))
        if not decoded:
            return match.group(0)
        key, params = decoded
        pattern = catalog(locale).get(key) or catalog(FALLBACK).get(key)
        if pattern is None:
            return key
        rendered = {name: render(item, locale) if isinstance(item, str) else item for name, item in params.items()}
        return format_message(locale, pattern, rendered)

    return _TOKEN.sub(replace, value)


# ---- The message formatter (ui/src/i18n/format.ts) ----

def _parse(pattern: str) -> list[tuple]:
    index = 0

    def nodes(in_plural: bool) -> list[tuple]:
        nonlocal index
        result: list[tuple] = []
        text = ""
        while index < len(pattern):
            char = pattern[index]
            if char == "}":
                break
            if char == "#" and in_plural:
                if text:
                    result.append(("text", text))
                    text = ""
                result.append(("hash",))
                index += 1
                continue
            if char != "{":
                text += char
                index += 1
                continue
            if text:
                result.append(("text", text))
                text = ""
            index += 1
            close = pattern.find("}", index)
            comma = pattern.find(",", index)
            if comma == -1 or (close != -1 and close < comma):
                result.append(("argument", pattern[index:close].strip(), "none"))
                index = close + 1
                continue
            name = pattern[index:comma].strip()
            index = comma + 1
            kind_end = re.search(r"[,}]", pattern[index:])
            kind_end_index = index + (kind_end.start() if kind_end else len(pattern) - index)
            kind = pattern[index:kind_end_index].strip()
            index = kind_end_index
            if kind == "number":
                index = pattern.find("}", index) + 1
                result.append(("argument", name, "number"))
                continue
            if kind not in ("plural", "select"):
                raise ValueError(f'Unsupported message format "{kind}" in: {pattern}')
            index += 1
            branches: dict[str, list[tuple]] = {}
            while index < len(pattern):
                while index < len(pattern) and pattern[index].isspace():
                    index += 1
                if pattern[index] == "}":
                    break
                open_index = pattern.find("{", index)
                selector = pattern[index:open_index].strip()
                index = open_index + 1
                branches[selector] = nodes(kind == "plural")
                index += 1
            index += 1
            result.append((kind, name, branches))
        if text:
            result.append(("text", text))
        return result

    return nodes(False)


_parsed: dict[str, list[tuple]] = {}


def format_number(locale: str, value: float) -> str:
    """Like Intl.NumberFormat(locale): grouped thousands, at most three decimals."""
    if isinstance(value, bool):
        value = int(value)
    if not math.isfinite(value):
        return str(value)
    negative = value < 0
    rounded = round(abs(value), 3)
    whole, _, fraction = f"{rounded:.3f}".partition(".")
    fraction = fraction.rstrip("0")
    group, decimal = (".", ",") if locale.startswith("pt") else (",", ".")
    grouped = ""
    while len(whole) > 3:
        grouped = group + whole[-3:] + grouped
        whole = whole[:-3]
    text = whole + grouped + (decimal + fraction if fraction else "")
    return "-" + text if negative and text.strip("0.,") else text


def plural_category(locale: str, value: float) -> str:
    """Intl.PluralRules(locale).select for the offered languages."""
    if locale.startswith("zh"):
        return "other"
    if locale.startswith("pt"):
        return "one" if 0 <= abs(value) < 2 else "other"
    return "one" if value == 1 else "other"


def _string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _branch(branches: dict[str, list[tuple]], *selectors: str) -> list[tuple]:
    # The first selector present wins, even when its branch is empty ("=0 {}").
    for selector in (*selectors, "other"):
        if selector in branches:
            return branches[selector]
    return []


def _render(locale: str, nodes: list[tuple], params: dict[str, Any], hash_value: float | None) -> str:
    out = []
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "hash":
            out.append("#" if hash_value is None else format_number(locale, hash_value))
        elif kind == "argument":
            value = params.get(node[1])
            if value is None:
                continue
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            out.append(format_number(locale, value) if node[2] == "number" and numeric else _string(value))
        elif kind == "plural":
            raw = params.get(node[1])
            try:
                value = float(raw if raw is not None else 0)
            except (TypeError, ValueError):
                value = 0.0
            out.append(_render(locale, _branch(node[2], f"={_string(value)}", plural_category(locale, value)), params, value))
        else:
            raw = params.get(node[1])
            out.append(_render(locale, _branch(node[2], "other" if raw is None else _string(raw)), params, hash_value))
    return "".join(out)


def format_message(locale: str, pattern: str, params: dict[str, Any] | None = None) -> str:
    nodes = _parsed.get(pattern)
    if nodes is None:
        nodes = _parsed[pattern] = _parse(pattern)
    return _render(locale, nodes, params or {}, None)


def message_arguments(pattern: str) -> list[str]:
    names: set[str] = set()

    def walk(nodes: list[tuple]) -> None:
        for node in nodes:
            if node[0] == "argument":
                names.add(node[1])
            elif node[0] in ("plural", "select"):
                names.add(node[1])
                for branch in node[2].values():
                    walk(branch)

    walk(_parse(pattern))
    return sorted(names)
