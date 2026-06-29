import json
import meeting_tool as mt


class TestNormalizeMeetingType:
    def test_fills_all_defaults_for_empty_dict(self):
        t = mt._normalize_meeting_type({})
        assert t == {
            "navn": "Møde",
            "detaljeniveau": "balanceret",
            "citater": False,
            "opgaveliste": True,
            "fokus": "",
            "ekstra_instruktioner": "",
            "deltagere": [],
        }

    def test_keeps_valid_values(self):
        raw = {
            "navn": "Ledergruppemøde",
            "detaljeniveau": "grundig",
            "citater": True,
            "opgaveliste": False,
            "fokus": "Strategi.",
            "ekstra_instruktioner": "Mere prosa.",
            "deltagere": ["Anna", "Bo"],
        }
        assert mt._normalize_meeting_type(raw) == raw

    def test_invalid_detaljeniveau_falls_back_to_balanceret(self):
        assert mt._normalize_meeting_type({"detaljeniveau": "xxx"})["detaljeniveau"] == "balanceret"

    def test_coerces_non_bool_and_filters_deltagere(self):
        t = mt._normalize_meeting_type({
            "citater": "ja", "opgaveliste": 0,
            "deltagere": ["  Dorte  ", 5, "", "Mads"],
        })
        assert t["citater"] is True
        assert t["opgaveliste"] is False
        assert t["deltagere"] == ["Dorte", "Mads"]

    def test_strips_navn_and_fokus(self):
        t = mt._normalize_meeting_type({"navn": "  X  ", "fokus": "  y "})
        assert t["navn"] == "X"
        assert t["fokus"] == "y"


class TestLoadMeetingTypes:
    def _write(self, tmp_path, name, data):
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")

    def test_seeds_from_default_when_missing(self, tmp_path):
        self._write(tmp_path, "meeting_types.default.json",
                    {"driftledelse": {"navn": "Driftledelsesmøde"}})
        types = mt.load_meeting_types(tmp_path)
        assert "driftledelse" in types
        assert types["driftledelse"]["navn"] == "Driftledelsesmøde"
        # filen blev skrevet
        assert (tmp_path / "meeting_types.json").exists()

    def test_returns_builtin_fallback_when_no_files(self, tmp_path):
        types = mt.load_meeting_types(tmp_path)
        assert "driftledelse" in types  # indbygget fallback

    def test_corrupt_json_falls_back_without_crash(self, tmp_path):
        (tmp_path / "meeting_types.json").write_text("{ ikke json", encoding="utf-8")
        types = mt.load_meeting_types(tmp_path)
        assert "driftledelse" in types

    def test_empty_dict_reseeds_to_at_least_one(self, tmp_path):
        self._write(tmp_path, "meeting_types.json", {})
        types = mt.load_meeting_types(tmp_path)
        assert len(types) >= 1

    def test_normalizes_each_type(self, tmp_path):
        self._write(tmp_path, "meeting_types.json",
                    {"x": {"navn": "X", "detaljeniveau": "ugyldig"}})
        types = mt.load_meeting_types(tmp_path)
        assert types["x"]["detaljeniveau"] == "balanceret"
        assert types["x"]["opgaveliste"] is True  # default fyldt


class TestComposeTypeSection:
    def _t(self, **kw):
        return mt._normalize_meeting_type(kw)

    def test_kortfattet_phrase_present(self):
        s = mt.compose_type_section(self._t(detaljeniveau="kortfattet"))
        assert "kort og fokuseret" in s.lower()

    def test_grundig_phrase_present(self):
        s = mt.compose_type_section(self._t(detaljeniveau="grundig"))
        assert "fyldigt" in s.lower()

    def test_citater_on_includes_quote_rule(self):
        s = mt.compose_type_section(self._t(citater=True))
        assert "citat" in s.lower()
        assert "ikke ordrette citater" not in s.lower()

    def test_citater_off_forbids_quotes(self):
        s = mt.compose_type_section(self._t(citater=False))
        assert "ikke ordrette citater" in s.lower()

    def test_opgaveliste_on_includes_section(self):
        s = mt.compose_type_section(self._t(opgaveliste=True))
        assert "Opgaveliste" in s
        assert "Uafklaret ansvar" in s

    def test_opgaveliste_off_excludes_section(self):
        s = mt.compose_type_section(self._t(opgaveliste=False))
        assert "Opgaveliste" not in s

    def test_fokus_injected_when_present(self):
        s = mt.compose_type_section(self._t(fokus="Strategi og retning."))
        assert "Strategi og retning." in s

    def test_fokus_absent_when_empty(self):
        s = mt.compose_type_section(self._t(fokus=""))
        assert "Læg særlig vægt på" not in s

    def test_ekstra_instruktioner_appended_verbatim(self):
        s = mt.compose_type_section(self._t(ekstra_instruktioner="HUSK CITATER ORDRET."))
        assert "HUSK CITATER ORDRET." in s


class TestResolveTypeAttendees:
    def test_prefers_remembered(self):
        out = mt.resolve_type_attendees(
            "drift", {"deltagere": ["TypeA"]},
            {"drift": ["Husket1", "Husket2"]}, ["Global"])
        assert out == ["Husket1", "Husket2"]

    def test_falls_back_to_type_deltagere(self):
        out = mt.resolve_type_attendees(
            "drift", {"deltagere": ["TypeA"]}, {}, ["Global"])
        assert out == ["TypeA"]

    def test_falls_back_to_global(self):
        out = mt.resolve_type_attendees(
            "drift", {"deltagere": []}, {}, ["Global"])
        assert out == ["Global"]

    def test_empty_remembered_list_is_ignored(self):
        out = mt.resolve_type_attendees(
            "drift", {"deltagere": ["TypeA"]}, {"drift": []}, ["Global"])
        assert out == ["TypeA"]


class TestGenerateMinutesWithType:
    def test_uses_type_section_grundig_citater(self, fake_gemini_client):
        import google.genai as _genai

        typ = mt._normalize_meeting_type(
            {"navn": "Ledergruppemøde", "detaljeniveau": "grundig", "citater": True}
        )
        out = mt.generate_minutes("[00:00 - 00:05] hej", ["Anna"], "01-06-2026", meeting_type=typ)
        assert out == fake_gemini_client.models.generate_content.return_value.text
        cfg = _genai.types.GenerateContentConfig.call_args
        system = cfg.kwargs["system_instruction"]
        assert "fyldigt" in system.lower()          # detaljeniveau-prosa
        assert "Ledergruppemøde" in system          # type-navn i rollen
        assert "ABSOLUT REGEL" in system             # base bevaret

    def test_none_type_falls_back_without_error(self, fake_gemini_client):
        result = mt.generate_minutes("x", ["A"], "01-06-2026")
        assert result == fake_gemini_client.models.generate_content.return_value.text


class TestGeminiInstructionTypeName:
    def test_format_includes_type_name(self):
        s = mt.GEMINI_SYSTEM_INSTRUCTION.format(
            meeting_type_name="ledergruppemøde",
            attendees="A",
            vocabulary_section="(ingen)",
            extra_context="(ingen)",
        )
        assert "ledergruppemøde" in s


class TestTranscribeFileForwardsMeetingType:
    """BUG C1 — transcribe_file skal videresende meeting_type til generate_minutes."""

    def test_meeting_type_forwarded_to_generate_minutes(self, tmp_path, monkeypatch):
        captured = {}

        def fake_generate_minutes(transcript, attendees, date, meeting_type=None):
            captured["meeting_type"] = meeting_type
            return "REFERAT"

        def fake_transcribe_audio(wav_path, model_size=None):
            return "transkription fra hviske"

        def fake_convert_to_wav(src, dst):
            dst.touch()
            return dst

        def fake_save_output(**kwargs):
            pass

        monkeypatch.setattr(mt, "generate_minutes", fake_generate_minutes)
        monkeypatch.setattr(mt, "transcribe_audio", fake_transcribe_audio)
        monkeypatch.setattr(mt, "convert_to_wav", fake_convert_to_wav)
        monkeypatch.setattr(mt, "save_output", fake_save_output)

        ledergruppe_type = mt._normalize_meeting_type({
            "navn": "Ledergruppemøde",
            "detaljeniveau": "grundig",
            "citater": True,
        })

        # Lav en falsk inputfil så stien eksisterer
        input_file = tmp_path / "optagelse.m4a"
        input_file.touch()

        mt.transcribe_file(
            input_path=input_file,
            output_dir=tmp_path / "ud",
            date="05-06-2026",
            name_base="test-møde",
            attendees=["Anna", "Bo"],
            engine="hviske",
            make_minutes=True,
            meeting_type=ledergruppe_type,
        )

        assert captured.get("meeting_type") == ledergruppe_type, (
            "transcribe_file videresendte ikke meeting_type til generate_minutes"
        )
