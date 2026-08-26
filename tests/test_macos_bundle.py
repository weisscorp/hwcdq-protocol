from __future__ import annotations

import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from tools import macos_bundle  # noqa: E402
from tools import build_macos_app  # noqa: E402


class MacOSBundleContractTests(unittest.TestCase):
    def test_bundle_identity_and_truthful_bluetooth_descriptions_are_stable(self) -> None:
        entries = macos_bundle.info_plist_entries()

        self.assertEqual(
            macos_bundle.BUNDLE_IDENTIFIER,
            "cc.hwcdq.bench-control",
        )
        self.assertEqual(
            macos_bundle.BUNDLE_NAME,
            "Pidzoom Portable charger HW178P",
        )
        self.assertEqual(
            macos_bundle.BUNDLE_EXECUTABLE,
            "Pidzoom Portable charger HW178P",
        )
        self.assertEqual(
            entries["CFBundleDisplayName"],
            "Pidzoom Portable charger HW178P",
        )
        self.assertEqual(
            entries["CFBundleName"],
            "Pidzoom Portable charger HW178P",
        )
        self.assertEqual(entries["LSMinimumSystemVersion"], "26.0")
        for key in (
            "NSBluetoothAlwaysUsageDescription",
            "NSBluetoothPeripheralUsageDescription",
        ):
            description = entries[key]
            self.assertIsInstance(description, str)
            self.assertIn("discover", description.casefold())
            self.assertIn("communicate", description.casefold())
            self.assertIn("charger", description.casefold())

    def test_metadata_validation_rejects_the_crashing_python_app_contract(self) -> None:
        issues = macos_bundle.validate_info_plist(
            {
                "CFBundleIdentifier": "org.python.python",
                "CFBundleExecutable": "Python",
            }
        )

        self.assertTrue(any("CFBundleIdentifier" in issue for issue in issues))
        self.assertTrue(
            any("NSBluetoothAlwaysUsageDescription" in issue for issue in issues)
        )
        self.assertTrue(
            any("NSBluetoothPeripheralUsageDescription" in issue for issue in issues)
        )

    def test_metadata_validation_accepts_a_serialized_final_plist(self) -> None:
        plist = {
            "CFBundleIdentifier": macos_bundle.BUNDLE_IDENTIFIER,
            "CFBundleExecutable": macos_bundle.BUNDLE_EXECUTABLE,
            **macos_bundle.info_plist_entries(),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Info.plist"
            path.write_bytes(plistlib.dumps(plist))
            loaded = plistlib.loads(path.read_bytes())

        self.assertEqual(macos_bundle.validate_info_plist(loaded), ())

    def test_frozen_bundle_requires_the_concrete_corebluetooth_backend(self) -> None:
        required = set(macos_bundle.required_corebluetooth_modules())

        self.assertIn("bleak.backends.corebluetooth.scanner", required)
        self.assertIn("bleak.backends.corebluetooth.client", required)
        self.assertIn("bleak.backends.corebluetooth.CentralManagerDelegate", required)
        self.assertIn("bleak.backends.corebluetooth.PeripheralDelegate", required)
        self.assertIn("bleak.backends.corebluetooth.utils", required)
        self.assertIn("bleak.backends.service", required)
        self.assertNotIn("bleak.backends.corebluetooth.service", required)

    def test_warning_audit_rejects_missing_required_runtime_modules(self) -> None:
        warning = (
            "missing module named bleak.backends.corebluetooth.scanner "
            "- imported by hwcdq_control.bleak_transport"
        )
        self.assertNotEqual(macos_bundle.audit_warning_text(warning), ())
        self.assertEqual(
            macos_bundle.audit_warning_text("missing module named winrt - optional"),
            (),
        )
        imported_by_required_module = (
            "missing module named 'collections.abc' - imported by "
            "bleak.backends.corebluetooth.scanner (top-level)"
        )
        self.assertEqual(
            macos_bundle.audit_warning_text(imported_by_required_module),
            (),
        )

    def test_build_keeps_pyinstaller_cache_inside_ignored_build_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = root / "HWCDQBenchControl.spec"
            spec.write_text("# test spec\n", encoding="utf-8")
            build_dir = root / "build" / "macos"
            dist_dir = root / "dist"
            app_path = dist_dir / f"{macos_bundle.BUNDLE_NAME}.app"
            app_path.mkdir(parents=True)
            with (
                patch.object(build_macos_app, "_require_macos"),
                patch.object(build_macos_app, "_require_pyinstaller"),
                patch.object(build_macos_app, "_run") as run,
            ):
                built = build_macos_app.build_app(
                    spec_path=spec,
                    build_dir=build_dir,
                    dist_dir=dist_dir,
                )

        self.assertEqual(built, app_path.resolve())
        environment = run.call_args.kwargs["env"]
        self.assertEqual(
            environment["PYINSTALLER_CONFIG_DIR"],
            str(build_dir.resolve() / "pyinstaller-cache"),
        )

    def test_deployment_target_parser_supports_modern_and_legacy_load_commands(
        self,
    ) -> None:
        output = """
Load command 1
      cmd LC_BUILD_VERSION
 platform MACOS
    minos 26.0
      sdk 26.4
Load command 2
      cmd LC_VERSION_MIN_MACOSX
  version 13.1
      sdk 13.3
"""
        self.assertEqual(
            macos_bundle.parse_macos_deployment_targets(output),
            ("26.0", "13.1"),
        )
        self.assertTrue(macos_bundle.is_macos_version_newer("26.0", "15.9"))
        self.assertFalse(macos_bundle.is_macos_version_newer("15.9", "26.0"))

    def test_bundle_macho_audit_rejects_newer_or_non_arm64_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "Fixture.app"
            binary = app / "Contents" / "Frameworks" / "fixture.dylib"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"\xcf\xfa\xed\xfe" + bytes(32))

            def newer_target(command: object, **_: object) -> subprocess.CompletedProcess[str]:
                rendered = tuple(str(part) for part in command)  # type: ignore[arg-type]
                if rendered[1] == "-archs":
                    return subprocess.CompletedProcess(rendered, 0, "arm64\n", "")
                return subprocess.CompletedProcess(
                    rendered,
                    0,
                    "Load command 1\n"
                    "      cmd LC_BUILD_VERSION\n"
                    " platform MACOS\n"
                    "    minos 27.0\n",
                    "",
                )

            with patch.object(build_macos_app, "_run", side_effect=newer_target):
                with self.assertRaisesRegex(
                    build_macos_app.BundleError,
                    "requires macOS 27.0",
                ):
                    build_macos_app._verify_macho_bundle(app)

            binary.write_bytes(b"\xca\xfe\xba\xbe" + bytes(32))

            def fat_arm64(command: object, **_: object) -> subprocess.CompletedProcess[str]:
                rendered = tuple(str(part) for part in command)  # type: ignore[arg-type]
                if rendered[1] == "-archs":
                    return subprocess.CompletedProcess(rendered, 0, "arm64\n", "")
                return subprocess.CompletedProcess(
                    rendered,
                    0,
                    "Load command 1\n"
                    "      cmd LC_BUILD_VERSION\n"
                    " platform MACOS\n"
                    "    minos 26.0\n",
                    "",
                )

            with patch.object(build_macos_app, "_run", side_effect=fat_arm64):
                with self.assertRaisesRegex(
                    build_macos_app.BundleError,
                    "fat Mach-O",
                ):
                    build_macos_app._verify_macho_bundle(app)

            binary.write_bytes(b"\xcf\xfa\xed\xfe" + bytes(32))

            def universal(command: object, **_: object) -> subprocess.CompletedProcess[str]:
                rendered = tuple(str(part) for part in command)  # type: ignore[arg-type]
                if rendered[1] == "-archs":
                    return subprocess.CompletedProcess(
                        rendered,
                        0,
                        "x86_64 arm64\n",
                        "",
                    )
                return subprocess.CompletedProcess(
                    rendered,
                    0,
                    "Load command 1\n"
                    "      cmd LC_BUILD_VERSION\n"
                    " platform MACOS\n"
                    "    minos 26.0\n",
                    "",
                )

            with patch.object(build_macos_app, "_run", side_effect=universal):
                with self.assertRaisesRegex(
                    build_macos_app.BundleError,
                    "thin arm64",
                ):
                    build_macos_app._verify_macho_bundle(app)

    def test_codesign_audit_requires_ad_hoc_identity_without_team(self) -> None:
        valid = (
            "Identifier=cc.hwcdq.bench-control\n"
            "Signature=adhoc\n"
            "TeamIdentifier=not set\n"
        )
        invalid_signature = valid.replace(
            "Signature=adhoc",
            "Signature=Developer ID",
        )
        invalid_team = valid.replace("TeamIdentifier=not set", "TeamIdentifier=TEAM123")

        def results_for(detail: str) -> list[subprocess.CompletedProcess[str]]:
            return [
                subprocess.CompletedProcess(("codesign",), 0, "", ""),
                subprocess.CompletedProcess(("codesign",), 0, "", detail),
            ]

        with patch.object(build_macos_app, "_run", side_effect=results_for(valid)):
            build_macos_app._verify_codesign(Path("Fixture.app"))
        with patch.object(
            build_macos_app,
            "_run",
            side_effect=results_for(invalid_signature),
        ):
            with self.assertRaisesRegex(build_macos_app.BundleError, "ad hoc"):
                build_macos_app._verify_codesign(Path("Fixture.app"))
        with patch.object(
            build_macos_app,
            "_run",
            side_effect=results_for(invalid_team),
        ):
            with self.assertRaisesRegex(build_macos_app.BundleError, "TeamIdentifier"):
                build_macos_app._verify_codesign(Path("Fixture.app"))


if __name__ == "__main__":
    unittest.main()
