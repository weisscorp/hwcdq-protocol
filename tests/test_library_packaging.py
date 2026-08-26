from __future__ import annotations

import ast
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = ROOT / "packages" / "hwcdq-client"
CORE_PACKAGE = CORE_ROOT / "src" / "hwcdq"
DESKTOP_PACKAGE = ROOT / "src" / "hwcdq_control"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return tuple(found)


class DistributionBoundaryTests(unittest.TestCase):
    def test_core_and_desktop_metadata_have_single_package_owner(self) -> None:
        core = tomllib.loads((CORE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        desktop = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(core["project"]["name"], "hwcdq-client")
        self.assertEqual(core["project"]["dependencies"], [])
        self.assertEqual(core["project"]["optional-dependencies"]["bleak"], ["bleak==3.0.2"])
        self.assertEqual(core["tool"]["setuptools"]["packages"], ["hwcdq"])

        self.assertEqual(desktop["project"]["name"], "hwcdq-control")
        self.assertIn("hwcdq-client==0.1.0", desktop["project"]["dependencies"])
        desktop_packages = desktop["tool"]["setuptools"]["packages"]
        self.assertNotIn("hwcdq", desktop_packages)
        self.assertTrue(all(not name.startswith("hwcdq.") for name in desktop_packages))

    def test_optional_dependencies_are_confined_to_their_adapters(self) -> None:
        for path in CORE_PACKAGE.glob("*.py"):
            imports = _imports(path)
            for imported in imports:
                self.assertFalse(imported.startswith("PySide6"), path)
                if imported == "bleak" or imported.startswith("bleak."):
                    self.assertEqual(path.name, "bleak.py")

    def test_desktop_frontend_consumes_canonical_library(self) -> None:
        frontend_paths = (
            DESKTOP_PACKAGE / "main.py",
            DESKTOP_PACKAGE / "qt_controller.py",
            DESKTOP_PACKAGE / "ui" / "main_window.py",
        )
        imported = {name for path in frontend_paths for name in _imports(path)}
        self.assertTrue(any(name == "hwcdq" or name.startswith("hwcdq.") for name in imported))
        self.assertFalse(any(name == "tools" or name.startswith("tools.") for name in imported))
        self.assertFalse(
            any(name == "hwcdq_control.backend" or name.startswith("hwcdq_control.backend.") for name in imported)
        )

    def test_frozen_app_searches_the_separate_core_source(self) -> None:
        spec = (ROOT / "packaging" / "macos" / "HWCDQBenchControl.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn('repository_root / "packages" / "hwcdq-client" / "src"', spec)
        self.assertIn("str(core_source_root)", spec)


if __name__ == "__main__":
    unittest.main()
