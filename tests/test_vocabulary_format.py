"""Tests for format_vocabulary_for_{claude,gemini,hviske}."""
from __future__ import annotations

import meeting_tool


def test_claude_all_categories():
    vocab = {
        "personer": ["Mads — driftsleder", "Anna — ejer"],
        "steder": ["Nordmarken"],
        "fagtermer": ["ramsning"],
    }
    out = meeting_tool.format_vocabulary_for_minutes(vocab)
    assert "**Personer:**" in out
    assert "- Mads — driftsleder" in out
    assert "- Anna — ejer" in out
    assert "**Steder:**" in out
    assert "- Nordmarken" in out
    assert "**Fagtermer:**" in out
    assert "- ramsning" in out


def test_claude_empty_category_skipped():
    vocab = {"personer": ["Mads"], "steder": [], "fagtermer": []}
    out = meeting_tool.format_vocabulary_for_minutes(vocab)
    assert "**Personer:**" in out
    assert "**Steder:**" not in out
    assert "**Fagtermer:**" not in out


def test_claude_all_empty_returns_placeholder():
    vocab = {"personer": [], "steder": [], "fagtermer": []}
    out = meeting_tool.format_vocabulary_for_minutes(vocab)
    assert out == "(ingen ordliste konfigureret)"


def test_gemini_all_categories():
    vocab = {
        "personer": ["Mads — driftsleder"],
        "steder": ["Nordmarken"],
        "fagtermer": ["ramsning"],
    }
    out = meeting_tool.format_vocabulary_for_gemini(vocab)
    assert "Personer" in out
    assert "Mads — driftsleder" in out
    assert "Steder" in out
    assert "Nordmarken" in out
    assert "Fagtermer" in out
    assert "ramsning" in out


def test_gemini_empty_category_skipped():
    vocab = {"personer": ["Mads"], "steder": [], "fagtermer": []}
    out = meeting_tool.format_vocabulary_for_gemini(vocab)
    assert "Personer" in out
    assert "Steder" not in out
    assert "Fagtermer" not in out


def test_gemini_all_empty_returns_placeholder():
    vocab = {"personer": [], "steder": [], "fagtermer": []}
    out = meeting_tool.format_vocabulary_for_gemini(vocab)
    assert out == "(ingen ordliste konfigureret)"


def test_hviske_term_part_only():
    """Note efter '—' eller '(' strippes; kun terms beholdes."""
    vocab = {
        "personer": ["Mads — driftsleder", "Anna (ejer)"],
        "steder": ["Nordmarken — eksempel-mark"],
        "fagtermer": ["ramsning — biased ikke i Hviske"],
    }
    out = meeting_tool.format_vocabulary_for_hviske(vocab)
    assert "Mads" in out
    assert "Anna" in out
    assert "Nordmarken" in out
    # Noter må ikke fremgå
    assert "driftsleder" not in out
    assert "ejer" not in out
    assert "eksempel-mark" not in out


def test_hviske_excludes_fagtermer():
    """Fagtermer udelades helt fra Hviske initial_prompt."""
    vocab = {
        "personer": ["Mads"],
        "steder": ["Nordmarken"],
        "fagtermer": ["ramsning", "radrensning"],
    }
    out = meeting_tool.format_vocabulary_for_hviske(vocab)
    assert "ramsning" not in out
    assert "radrensning" not in out


def test_hviske_order_personer_first():
    vocab = {
        "personer": ["Mads"],
        "steder": ["Nordmarken"],
        "fagtermer": [],
    }
    out = meeting_tool.format_vocabulary_for_hviske(vocab)
    assert out.index("Mads") < out.index("Nordmarken")


def test_hviske_truncates_to_char_limit():
    """Lange vocabularies trunkeres så de holder sig under ~800 tegn."""
    vocab = {
        "personer": [f"Person{i}" for i in range(500)],
        "steder": [f"Sted{i}" for i in range(500)],
        "fagtermer": [],
    }
    out = meeting_tool.format_vocabulary_for_hviske(vocab)
    assert len(out) <= 800


def test_hviske_empty_returns_empty_string():
    vocab = {"personer": [], "steder": [], "fagtermer": []}
    out = meeting_tool.format_vocabulary_for_hviske(vocab)
    assert out == ""


def test_hviske_default_content_under_limit():
    """Default-indholdet skal kunne være i Hviske initial_prompt."""
    vocab = {
        "personer": [
            "Mads — driftsleder. ALDRIG 'Mats'; i tvivl: vælg Mads",
            "Anna Holm — ejer",
            "Bo Holm — ejer",
            "Henrik — IT",
            "Dorte, Erik, Frederik, Gitte, Lars — medarbejdere",
        ],
        "steder": [
            "Nordmarken — eksempel-mark",
            "Sydmarken — eksempel-mark",
            "Vestgården — ekstern samarbejdsgård",
            "Nordgården — leverandør",
            "Eksempelgården",
        ],
        "fagtermer": ["irrelevant"],
    }
    out = meeting_tool.format_vocabulary_for_hviske(vocab)
    assert len(out) <= 800
    assert "Mads" in out
    assert "Nordmarken" in out


def test_system_prompt_placeholder_filled():
    """SYSTEM_PROMPT skal indeholde alle fire placeholders, og format() med
    gyldige værdier skal producere et komplet prompt-tekst uden tomme
    placeholders. Asserter på den stabile organisations-base (attributions-
    reglen og emneprioritering), ikke mødetype-specifikt indhold."""
    assert "{vocabulary_section}" in meeting_tool.SYSTEM_PROMPT
    assert "{meeting_type_name}" in meeting_tool.SYSTEM_PROMPT
    assert "{meeting_type_section}" in meeting_tool.SYSTEM_PROMPT
    formatted = meeting_tool.SYSTEM_PROMPT.format(
        meeting_type_name="Driftledelsesmøde",
        attendees="A, B",
        vocabulary_section="**Personer:**\n- Mads",
        meeting_type_section="## SÅDAN SKAL DETTE REFERAT VÆRE\n\nKort.",
    )
    assert "**Personer:**" in formatted
    assert "- Mads" in formatted
    assert "{vocabulary_section}" not in formatted
    assert "ABSOLUT REGEL" in formatted          # attributions-reglen bevaret
    assert "EMNEPRIORITERING" in formatted       # prioriteringslaget bevaret
    assert "Driftledelsesmøde" in formatted      # type-navn indsat


def test_gemini_system_instruction_placeholder_filled():
    """GEMINI_SYSTEM_INSTRUCTION skal indeholde alle fire placeholders, og
    format() med gyldige værdier skal producere en komplet instruktion."""
    assert "{vocabulary_section}" in meeting_tool.GEMINI_SYSTEM_INSTRUCTION
    assert "{meeting_type_name}" in meeting_tool.GEMINI_SYSTEM_INSTRUCTION
    formatted = meeting_tool.GEMINI_SYSTEM_INSTRUCTION.format(
        meeting_type_name="driftsledelsesmøde",
        attendees="A, B",
        vocabulary_section="**Personer:**\n- Mads",
        extra_context="(intet)",
    )
    assert "**Personer:**" in formatted
    assert "- Mads" in formatted
    assert "{vocabulary_section}" not in formatted
    assert "driftsledelsesmøde" in formatted


import meeting_tool as mt


class TestSplitJoinVocabEntry:
    def test_split_with_separator(self):
        s = f"Lars{mt._VOCAB_SEP}kører traktor"
        assert mt.split_vocab_entry(s) == ("Lars", "kører traktor")

    def test_split_without_separator(self):
        assert mt.split_vocab_entry("Ole") == ("Ole", "")

    def test_split_strips_whitespace(self):
        s = f"  Mads  {mt._VOCAB_SEP}   søn  "
        assert mt.split_vocab_entry(s) == ("Mads", "søn")

    def test_split_on_first_separator_only(self):
        s = f"A{mt._VOCAB_SEP}B{mt._VOCAB_SEP}C"
        assert mt.split_vocab_entry(s) == ("A", f"B{mt._VOCAB_SEP}C")

    def test_split_empty(self):
        assert mt.split_vocab_entry("") == ("", "")

    def test_join_with_forklaring(self):
        assert mt.join_vocab_entry("Lars", "kører traktor") == f"Lars{mt._VOCAB_SEP}kører traktor"

    def test_join_without_forklaring(self):
        assert mt.join_vocab_entry("Ole", "") == "Ole"

    def test_join_strips(self):
        assert mt.join_vocab_entry("  Mads ", "  søn ") == f"Mads{mt._VOCAB_SEP}søn"

    def test_join_empty_word_returns_empty(self):
        assert mt.join_vocab_entry("", "kun forklaring") == ""

    def test_round_trip_with_forklaring(self):
        s = f"Mads{mt._VOCAB_SEP}driftsleder"
        assert mt.join_vocab_entry(*mt.split_vocab_entry(s)) == s

    def test_round_trip_without_forklaring(self):
        assert mt.join_vocab_entry(*mt.split_vocab_entry("Ole")) == "Ole"
