# modevaerktoj.spec — PyInstaller one-folder build (Windows).
# Bygges af CI (.github/workflows/release-windows.yml): pyinstaller modevaerktoj.spec
# Output: dist/Mødeværktøj/ (mappe med exe + DLL'er + bundlede ffmpeg/defaults).
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# --- CustomTkinter: saml pakke-kode + temaer/assets + submoduler (darkdetect
# m.v.) robust. collect_data_files alene tager IKKE Python-koden med. ---
ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")

# python-docx skibber en default-skabelon (docx/templates/default.docx) som
# pakke-data — Document() fejler uden den, så collect_all bundler alt.
docx_datas, docx_binaries, docx_hiddenimports = collect_all("docx")

# --- Data-filer ---
datas = []
datas += ctk_datas
datas += docx_datas
# Read-only templates — app_paths.ensure_user_config seeder dem til %APPDATA%
# ved første kørsel. De ligger i app-roden ved siden af exe'en.
datas += [
    ("vocabulary.default.json", "."),
    ("meeting_types.default.json", "."),
    ("AppIcon.ico", "."),   # vinduesikon (root.iconbitmap ved kørsel)
]

# --- Binærer ---
# ffmpeg.exe/ffprobe.exe lægges i repo-roden af CI FØR pyinstaller kører.
# app_paths.resolve_binary("ffmpeg") finder dem i app-roden ved kørsel.
binaries = [
    ("ffmpeg.exe", "."),
    ("ffprobe.exe", "."),
]
binaries += ctk_binaries
binaries += docx_binaries

a = Analysis(
    ["meeting_app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    # Lokale moduler der importeres lazily (updater i meeting_app, app_paths i
    # audio_routing) — tilføjet eksplicit så PyInstaller ikke misser dem.
    # Tredjeparts-pakker med dynamiske/lazy imports listes også.
    hiddenimports=[
        "updater",
        "app_paths",
        "_version",
        "audio_routing",
        "meeting_tool",
        "transcript_merge",
        "faster_whisper",
        "google.genai",
        "anthropic",
        "pydub",
        "docx",
    ] + ctk_hiddenimports + docx_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Mødeværktøj",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # UPX-pakkede .exe'er flag'es ofte af Windows Defender
    console=False,          # GUI-app — intet terminalvindue
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="AppIcon.ico",     # exe-ikon (Windows Stifinder / proceslinje)
    version="version_info.txt",  # firma/produkt-metadata → færre AV-falske-positiver
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Mødeværktøj",     # → dist/Mødeværktøj/ (one-folder)
)
