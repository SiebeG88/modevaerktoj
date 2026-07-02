"""Tests for to-spors start-stempel-resolution og sidecar."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


def test_resolve_sync_uses_stamps(tmp_path):
    mic = tmp_path / "m.mic.wav"; mic.write_bytes(b"x" * 2000)
    sys_ = tmp_path / "m.sys.wav"; sys_.write_bytes(b"x" * 2000)
    res = mt._resolve_sync({"mic": 1000.0, "sys": 996.0}, mic, sys_, True,
                           now=2000.0, duration_fn=lambda p: 42.0)
    assert res.sys_ok is True
    assert res.mic_start == 1000.0
    assert res.sys_start == 996.0
    assert res.mic_duration == 42.0
    assert res.sys_duration == 42.0


def test_resolve_sync_fallbacks_when_sys_missing(tmp_path):
    mic = tmp_path / "m.mic.wav"; mic.write_bytes(b"x" * 2000)
    sys_ = tmp_path / "m.sys.wav"  # findes ikke
    res = mt._resolve_sync({"mic": None, "sys": None}, mic, sys_, False,
                           now=2000.0, duration_fn=lambda p: 42.0)
    assert res.sys_ok is False
    assert res.mic_start == 2000.0       # fallback til now
    assert res.sys_start == 2000.0       # = mic_start
    assert res.sys_duration == 0.0


def test_write_sync_sidecar_roundtrip(tmp_path):
    res = mt.DualTrackResult(True, 1000.0, 996.0, 42.0, 50.0)
    p = tmp_path / "m.sync.json"
    mt._write_sync_sidecar(p, res)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["mic_start"] == 1000.0
    assert data["sys_start"] == 996.0
    assert data["sys_duration"] == 50.0
