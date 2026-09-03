import hashlib
import io
import json

import pytest

import updater


# ── Part A: version parsing/compare ────────────────────────────────────────

def test_parse_version_strips_v_prefix():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.0.0") == (1, 0, 0)


def test_is_newer_true():
    assert updater.is_newer("v1.2.0", local="1.1.9") is True


def test_is_newer_false_when_equal():
    assert updater.is_newer("v1.0.0", local="1.0.0") is False


def test_is_newer_false_when_older():
    assert updater.is_newer("v0.9.0", local="1.0.0") is False


# ── Part B: check_for_update ────────────────────────────────────────────────

def test_check_for_update_returns_release_when_newer(mocker):
    payload = {
        "tag_name": "v2.0.0",
        "assets": [
            {"name": "Modevaerktoj-v2.0.0.zip",
             "browser_download_url": "https://x/app.zip"},
            {"name": "Modevaerktoj-v2.0.0.zip.sha256",
             "browser_download_url": "https://x/app.zip.sha256"},
        ],
    }
    mocker.patch("urllib.request.urlopen",
                 return_value=io.BytesIO(json.dumps(payload).encode()))
    rel = updater.check_for_update(local="1.0.0")
    assert rel is not None
    assert rel.tag == "v2.0.0"
    assert rel.asset_url == "https://x/app.zip"
    assert rel.sha256_url == "https://x/app.zip.sha256"


def test_check_for_update_none_when_not_newer(mocker):
    payload = {"tag_name": "v1.0.0", "assets": []}
    mocker.patch("urllib.request.urlopen",
                 return_value=io.BytesIO(json.dumps(payload).encode()))
    assert updater.check_for_update(local="1.0.0") is None


def test_check_for_update_none_on_network_error(mocker):
    mocker.patch("urllib.request.urlopen", side_effect=OSError("offline"))
    assert updater.check_for_update(local="1.0.0") is None


def test_check_for_update_none_when_no_sha256_asset(mocker):
    # Nyere version, men kun .zip (ingen checksum) → afvis: ingen uverificeret
    # binær må installeres.
    payload = {
        "tag_name": "v2.0.0",
        "assets": [
            {"name": "Modevaerktoj-v2.0.0.zip",
             "browser_download_url": "https://x/app.zip"},
        ],
    }
    mocker.patch("urllib.request.urlopen",
                 return_value=io.BytesIO(json.dumps(payload).encode()))
    assert updater.check_for_update(local="1.0.0") is None


def test_check_for_update_none_on_non_dict_payload(mocker):
    # Uventet svar (JSON-liste i stedet for objekt) → None, ikke AttributeError.
    mocker.patch("urllib.request.urlopen",
                 return_value=io.BytesIO(json.dumps([1, 2, 3]).encode()))
    assert updater.check_for_update(local="1.0.0") is None


# ── Part C: download_and_stage ──────────────────────────────────────────────

def test_download_and_stage_verifies_sha256(tmp_path, mocker):
    content = b"zip-bytes-her"
    good = hashlib.sha256(content).hexdigest()
    rel = updater.Release(tag="v2.0.0", asset_url="https://x/app.zip",
                          sha256_url="https://x/app.zip.sha256")
    mocker.patch.object(updater, "_fetch_bytes", side_effect=lambda u: content)
    mocker.patch.object(updater, "_fetch_text", side_effect=lambda u: good)
    out = updater.download_and_stage(rel, tmp_path)
    assert (out / "app.zip").read_bytes() == content


def test_download_and_stage_raises_on_mismatch(tmp_path, mocker):
    rel = updater.Release(tag="v2.0.0", asset_url="https://x/app.zip",
                          sha256_url="https://x/app.zip.sha256")
    mocker.patch.object(updater, "_fetch_bytes", side_effect=lambda u: b"data")
    mocker.patch.object(updater, "_fetch_text", side_effect=lambda u: "deadbeef")
    with pytest.raises(updater.UpdateError):
        updater.download_and_stage(rel, tmp_path)


def test_download_and_stage_raises_when_no_sha256_url(tmp_path, mocker):
    # Defense-in-depth: en Release uden checksum-URL må aldrig skrives til disk.
    rel = updater.Release(tag="v2.0.0", asset_url="https://x/app.zip",
                          sha256_url="")
    fetch = mocker.patch.object(updater, "_fetch_bytes", side_effect=lambda u: b"x")
    with pytest.raises(updater.UpdateError):
        updater.download_and_stage(rel, tmp_path)
    fetch.assert_not_called()  # afvist før download overhovedet startes
    assert not (tmp_path / "app.zip").exists()


def test_download_and_stage_raises_on_empty_checksum_file(tmp_path, mocker):
    # Tom checksum-fil → tomt forventet hash → mismatch, ingen fil skrevet.
    rel = updater.Release(tag="v2.0.0", asset_url="https://x/app.zip",
                          sha256_url="https://x/app.zip.sha256")
    mocker.patch.object(updater, "_fetch_bytes", side_effect=lambda u: b"data")
    mocker.patch.object(updater, "_fetch_text", side_effect=lambda u: "")
    with pytest.raises(updater.UpdateError):
        updater.download_and_stage(rel, tmp_path)
    assert not (tmp_path / "app.zip").exists()


# ── Program Files-flytning: skrivbarheds-tjek + helper-script ───────────────

def test_dir_writable_true(tmp_path):
    assert updater._dir_writable(tmp_path) is True


def test_dir_writable_false_for_missing_dir(tmp_path):
    assert updater._dir_writable(tmp_path / "findes-ikke") is False


def test_helper_script_elevated_restarts_via_explorer(tmp_path):
    """Eleveret swap må ikke genstarte appen med admin-rettigheder."""
    s = updater._build_helper_script(
        tmp_path / "app", tmp_path / "new", tmp_path / "app" / "x.exe",
        tmp_path / "ota.log", 123, elevated=True)
    assert "explorer.exe" in s
    assert "StartApp" in s


def test_helper_script_unelevated_starts_exe_directly(tmp_path):
    s = updater._build_helper_script(
        tmp_path / "app", tmp_path / "new", tmp_path / "app" / "x.exe",
        tmp_path / "ota.log", 123, elevated=False)
    assert "explorer.exe" not in s
    assert "Start-Process -FilePath $exe" in s


def test_helper_script_quotes_paths_and_pid(tmp_path):
    app = tmp_path / "Mødeværktøj"
    s = updater._build_helper_script(
        app, tmp_path / "new", app / "Mødeværktøj.exe",
        tmp_path / "ota.log", 4321, elevated=False)
    assert "$procId=4321" in s
    assert str(app) in s
