"""Palette-tokens er ren data — testes uden display."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


REQUIRED = {
    "sidebar_bg": "#1b2a4a", "sidebar_icon": "#7f92b8",
    "sidebar_icon_active": "#ffffff", "bg": "#f7f8fa", "card": "#ffffff",
    "card_border": "#e6e8ec", "accent": "#3b6fe0", "accent_hover": "#2f5ec9",
    "text": "#1b2a4a", "text_secondary": "#5b6474", "rec_active": "#e5382b",
    "badge_ok": "#2e7d5b", "badge_ok_bg": "#e3f5ec",
    "badge_busy": "#8a6d00", "badge_busy_bg": "#fbf3d8",
}


def test_palette_has_new_tokens_with_exact_values():
    for key, val in REQUIRED.items():
        assert meeting_app._CLR[key].lower() == val, key


def test_legacy_keys_still_present():
    # Resten af filen slår stadig disse op — de må ikke forsvinde.
    for key in ("rec_idle", "rec_ring", "stop_blue", "stop_hover",
                "success", "text_placeholder", "accent_hover"):
        assert key in meeting_app._CLR
