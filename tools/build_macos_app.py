"""Build and independently verify the local HWCDQ macOS application bundle.

The verifier only runs metadata checks, import-only runtime checks, and the
in-process simulator.  It never constructs a BLE scanner or communicates with
Bluetooth hardware.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from tools.macos_bundle import (
    BUNDLE_EXECUTABLE,
    BUNDLE_IDENTIFIER,
    BUNDLE_NAME,
    MINIMUM_MACOS_VERSION,
    audit_warning_text,
    is_macos_version_newer,
    parse_macos_deployment_targets,
    validate_info_plist,
)


REPOSITORY_ROOT = _REPOSITORY_ROOT
DEFAULT_SPEC_PATH = REPOSITORY_ROOT / "packaging" / "macos" / "HWCDQBenchControl.spec"
DEFAULT_BUILD_DIR = REPOSITORY_ROOT / "build" / "macos"
DEFAULT_DIST_DIR = REPOSITORY_ROOT / "dist"
DEFAULT_APP_PATH = DEFAULT_DIST_DIR / f"{BUNDLE_NAME}.app"
PYINSTALLER_VERSION = "6.22.2"
_THIN_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
}
_FAT_MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}
_MACHO_MAGICS = _THIN_MACHO_MAGICS | _FAT_MACHO_MAGICS


class BundleError(RuntimeError):
    """Raised when the bundle cannot be built or violates its contract."""


def _run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path = REPOSITORY_ROOT,
    env: Mapping[str, str] | None = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    rendered = [os.fspath(part) for part in command]
    try:
        result = subprocess.run(
            rendered,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BundleError(f"required executable not found: {rendered[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BundleError(
            f"command timed out after {timeout:g}s: {' '.join(rendered)}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no diagnostic output").strip()
        raise BundleError(
            f"command failed ({result.returncode}): {' '.join(rendered)}\n{detail}"
        )
    return result


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise BundleError("the HWCDQ .app can only be built and verified on macOS")


def _require_pyinstaller() -> None:
    try:
        installed = importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError as exc:
        raise BundleError(
            "PyInstaller is not installed; install the declared macos-app extra first"
        ) from exc
    if installed != PYINSTALLER_VERSION:
        raise BundleError(
            f"PyInstaller {PYINSTALLER_VERSION} is required; found {installed}"
        )


def build_app(
    *,
    spec_path: Path = DEFAULT_SPEC_PATH,
    build_dir: Path = DEFAULT_BUILD_DIR,
    dist_dir: Path = DEFAULT_DIST_DIR,
) -> Path:
    """Build a clean onedir/windowed app from the committed spec."""

    _require_macos()
    _require_pyinstaller()
    spec_path = spec_path.resolve()
    build_dir = build_dir.resolve()
    dist_dir = dist_dir.resolve()
    if not spec_path.is_file():
        raise BundleError(f"PyInstaller spec is missing: {spec_path}")

    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "0"
    environment["PYINSTALLER_CONFIG_DIR"] = os.fspath(
        build_dir / "pyinstaller-cache"
    )
    _run(
        (
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--workpath",
            build_dir,
            "--distpath",
            dist_dir,
            spec_path,
        ),
        env=environment,
        timeout=900,
    )
    app_path = dist_dir / f"{BUNDLE_NAME}.app"
    if not app_path.is_dir():
        raise BundleError(f"PyInstaller completed without creating {app_path}")
    return app_path


def _load_and_validate_plist(app_path: Path) -> Path:
    plist_path = app_path / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise BundleError(f"bundle Info.plist is missing: {plist_path}")
    _run(("/usr/bin/plutil", "-lint", plist_path))
    try:
        loaded = plistlib.loads(plist_path.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise BundleError(f"cannot parse bundle Info.plist: {exc}") from exc
    if not isinstance(loaded, dict):
        raise BundleError("bundle Info.plist root must be a dictionary")
    issues = validate_info_plist(loaded)
    if issues:
        raise BundleError("invalid bundle metadata:\n- " + "\n- ".join(issues))
    return plist_path


def _audit_pyinstaller_warnings(build_dir: Path) -> tuple[Path, ...]:
    reports = tuple(sorted(build_dir.rglob("warn-*.txt")))
    if not reports:
        raise BundleError(f"no PyInstaller warning report found under {build_dir}")
    issues: list[str] = []
    for report in reports:
        try:
            warnings = audit_warning_text(report.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BundleError(f"cannot read PyInstaller warning report {report}: {exc}") from exc
        issues.extend(f"{report}: {warning}" for warning in warnings)
    if issues:
        raise BundleError(
            "required CoreBluetooth modules are missing from the frozen build:\n- "
            + "\n- ".join(issues)
        )
    return reports


def _verify_codesign(app_path: Path) -> None:
    _run(
        (
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            app_path,
        )
    )
    detail = _run(("/usr/bin/codesign", "-dv", "--verbose=4", app_path))
    combined = "\n".join(part for part in (detail.stdout, detail.stderr) if part)
    details = {
        key: value.strip()
        for key, value in re.findall(
            r"^(Identifier|Signature|TeamIdentifier)=(.+)$",
            combined,
            re.MULTILINE,
        )
    }
    identifier = details.get("Identifier")
    if identifier is None:
        raise BundleError("codesign metadata did not contain an Identifier line")
    if identifier != BUNDLE_IDENTIFIER:
        raise BundleError(
            f"codesign Identifier must be {BUNDLE_IDENTIFIER!r}; found {identifier!r}"
        )
    signature = details.get("Signature")
    if signature != "adhoc":
        raise BundleError(
            "bundle must use an ad hoc signature; "
            f"codesign reported {signature!r}"
        )
    team_identifier = details.get("TeamIdentifier")
    if team_identifier != "not set":
        raise BundleError(
            "ad hoc bundle must not have a TeamIdentifier; "
            f"codesign reported {team_identifier!r}"
        )


def _macho_magic(path: Path) -> bytes | None:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return None
        with path.open("rb") as stream:
            magic = stream.read(4)
    except OSError as exc:
        raise BundleError(f"cannot inspect bundled file {path}: {exc}") from exc
    return magic if magic in _MACHO_MAGICS else None


def _verify_macho_bundle(app_path: Path) -> tuple[Path, ...]:
    """Require thin arm64 Mach-O files compatible with the declared minimum."""

    contents = app_path / "Contents"
    macho_entries = tuple(
        (path, magic)
        for path in sorted(contents.rglob("*"))
        if (magic := _macho_magic(path)) is not None
    )
    if not macho_entries:
        raise BundleError(f"bundle contains no Mach-O files under {contents}")

    issues: list[str] = []
    for path, magic in macho_entries:
        if magic in _FAT_MACHO_MAGICS:
            issues.append(f"{path} must be thin arm64; found fat Mach-O container")
        architecture = _run(("/usr/bin/lipo", "-archs", path))
        architectures = set(architecture.stdout.split())
        if architectures != {"arm64"}:
            issues.append(
                f"{path} must be thin arm64; found "
                f"{architecture.stdout.strip()!r}"
            )

        build = _run(("/usr/bin/vtool", "-show-build", path))
        targets = parse_macos_deployment_targets(build.stdout)
        if not targets:
            issues.append(f"{path} has no readable macOS deployment target")
            continue
        for target in targets:
            if is_macos_version_newer(target, MINIMUM_MACOS_VERSION):
                issues.append(
                    f"{path} requires macOS {target}, newer than declared "
                    f"{MINIMUM_MACOS_VERSION}"
                )

    if issues:
        raise BundleError("invalid bundled Mach-O files:\n- " + "\n- ".join(issues))
    return tuple(path for path, _ in macho_entries)


def _verify_qt_cocoa_plugin(app_path: Path) -> Path:
    candidates = tuple(
        path
        for path in (app_path / "Contents").rglob("libqcocoa.dylib")
        if path.is_file()
        and path.parent.name == "platforms"
        and "plugins" in path.parts
    )
    if not candidates:
        raise BundleError(
            "bundle does not contain the Qt Cocoa platform plugin "
            "(plugins/platforms/libqcocoa.dylib)"
        )
    return candidates[0]


def _verify_frozen_commands(executable: Path) -> None:
    _run((executable, "--version"), timeout=20)
    # --self-check imports the backend only.  It never constructs a scanner,
    # client, or CoreBluetooth manager.
    _run((executable, "--self-check"), timeout=30)


def _read_events(path: Path) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise BundleError(f"cannot read simulator startup log {path}: {exc}") from exc
    events: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = record.get("event") if isinstance(record, dict) else None
        if isinstance(event, str):
            events.append(event)
    return tuple(events)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            raise BundleError(
                f"frozen simulator process {process.pid} did not terminate"
            ) from exc


def _verify_simulator_startup(executable: Path, *, timeout: float) -> None:
    if timeout <= 0:
        raise BundleError("simulator startup timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="hwcdq-bundle-verify-") as directory:
        # macOS exposes /var as a symlink to /private/var.  The diagnostic
        # logger intentionally rejects symlinked ancestors, so canonicalize
        # TemporaryDirectory's spelling before handing the path to the app.
        private_directory = Path(directory).resolve()
        debug_log = private_directory / "startup.jsonl"
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        command = (
            os.fspath(executable),
            "--simulate",
            "--debug",
            "--debug-log",
            os.fspath(debug_log),
            "--scan-seconds",
            "0.1",
        )
        process: subprocess.Popen[bytes] | None = None
        observed: tuple[str, ...] = ()
        try:
            process = subprocess.Popen(
                command,
                cwd=private_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                observed = _read_events(debug_log)
                if "window_shown" in observed:
                    return
                return_code = process.poll()
                if return_code is not None:
                    raise BundleError(
                        "frozen simulator exited before window_shown "
                        f"(status {return_code}; events={observed!r})"
                    )
                time.sleep(0.05)
            raise BundleError(
                f"frozen simulator did not emit window_shown within {timeout:g}s "
                f"(events={observed!r})"
            )
        finally:
            if process is not None:
                _terminate_process(process)


def verify_app(
    *,
    app_path: Path = DEFAULT_APP_PATH,
    build_dir: Path = DEFAULT_BUILD_DIR,
    startup_timeout: float = 20.0,
) -> None:
    """Verify metadata, signing, frozen imports, and simulated startup."""

    _require_macos()
    app_path = app_path.resolve()
    build_dir = build_dir.resolve()
    if not app_path.is_dir():
        raise BundleError(f"application bundle is missing: {app_path}")
    _load_and_validate_plist(app_path)
    _audit_pyinstaller_warnings(build_dir)

    executable = app_path / "Contents" / "MacOS" / BUNDLE_EXECUTABLE
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise BundleError(f"bundle executable is missing or not executable: {executable}")
    _verify_qt_cocoa_plugin(app_path)
    _verify_macho_bundle(app_path)
    _verify_codesign(app_path)
    _verify_frozen_commands(executable)
    _verify_simulator_startup(executable, timeout=startup_timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and verify the local HWCDQ macOS application",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build the .app from the pinned spec")
    build.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    build.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    build.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR)

    verify = subparsers.add_parser("verify", help="verify an existing .app")
    verify.add_argument("--app", type=Path, default=DEFAULT_APP_PATH)
    verify.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    verify.add_argument("--startup-timeout", type=float, default=20.0)

    all_steps = subparsers.add_parser("all", help="build, then verify the .app")
    all_steps.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    all_steps.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    all_steps.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR)
    all_steps.add_argument("--startup-timeout", type=float, default=20.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            app_path = build_app(
                spec_path=args.spec,
                build_dir=args.build_dir,
                dist_dir=args.dist_dir,
            )
            print(f"Built {app_path}")
        elif args.command == "verify":
            verify_app(
                app_path=args.app,
                build_dir=args.build_dir,
                startup_timeout=args.startup_timeout,
            )
            print(f"Verified {args.app.resolve()}")
        else:
            app_path = build_app(
                spec_path=args.spec,
                build_dir=args.build_dir,
                dist_dir=args.dist_dir,
            )
            verify_app(
                app_path=app_path,
                build_dir=args.build_dir,
                startup_timeout=args.startup_timeout,
            )
            print(f"Built and verified {app_path}")
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
