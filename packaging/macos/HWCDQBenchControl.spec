# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


repository_root = Path(SPECPATH).resolve().parents[1]
source_root = repository_root / "src"
core_source_root = repository_root / "packages" / "hwcdq-client" / "src"
sys.path.insert(0, str(repository_root))

from tools.macos_bundle import (  # noqa: E402
    BUNDLE_EXECUTABLE,
    BUNDLE_IDENTIFIER,
    BUNDLE_NAME,
    info_plist_entries,
)


hiddenimports = sorted(
    {
        *collect_submodules("bleak.backends.corebluetooth"),
        "bleak.backends.service",
    }
)

a = Analysis(
    [str(repository_root / "packaging" / "macos" / "entrypoint.py")],
    pathex=[str(repository_root), str(source_root), str(core_source_root)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=BUNDLE_EXECUTABLE,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=BUNDLE_EXECUTABLE,
)
app = BUNDLE(
    coll,
    name=f"{BUNDLE_NAME}.app",
    icon=None,
    bundle_identifier=BUNDLE_IDENTIFIER,
    info_plist=info_plist_entries(),
)
