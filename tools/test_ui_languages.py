"""The backend accepts exactly the interface languages the UI offers."""
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import chimera_web as web

LOCALES = Path(__file__).resolve().parents[1] / "ui" / "src" / "i18n" / "locales.ts"


def offered_languages() -> tuple[str, ...]:
    return tuple(re.findall(r"\{\s*code:\s*'([^']+)'", LOCALES.read_text(encoding="utf-8")))


def test_backend_languages_match_the_ui() -> None:
    assert sorted(offered_languages()) == sorted(web.UI_LANGUAGES)


def test_language_preference_round_trip() -> None:
    service = object.__new__(web.ChimeraService)
    service.lock = web.threading.Lock()
    with TemporaryDirectory() as directory, patch.object(web, "UI_PREFERENCES", Path(directory) / "preferences.json"):
        # Nothing saved yet: no language, so the window follows the system language.
        assert service.ui_preferences() == {"language": None}
        for language in web.UI_LANGUAGES:
            assert service.save_ui_preferences({"language": language}) == {"language": language}
            assert service.ui_preferences() == {"language": language}
        for bad in ({"language": "pt"}, {"language": None}, {}, "pt-BR"):
            try:
                service.save_ui_preferences(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(bad)
        assert service.ui_preferences() == {"language": web.UI_LANGUAGES[-1]}
