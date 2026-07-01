"""historik_badge er en ren mapping — testes uden display."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


def test_badge_klar():
    label, fg, bg = meeting_app.historik_badge("klar")
    assert label == "Referat klar"
    assert fg.lower() == "#2e7d5b" and bg.lower() == "#e3f5ec"


def test_badge_i_gang():
    label, fg, bg = meeting_app.historik_badge("i_gang")
    assert "ranskrib" in label.lower()
    assert fg.lower() == "#8a6d00" and bg.lower() == "#fbf3d8"


def test_badge_none():
    assert meeting_app.historik_badge("") == ("", "", "")
