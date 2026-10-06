from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "bridge" / "local_mini_package" / "mini_local_mcp_v2.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "mini_local_mcp_v2_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LocalMiniPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_write_text_is_atomic_and_returns_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            target = root / "a.txt"
            env = {
                "MINI_MCP_READ_ROOTS": str(root),
                "MINI_MCP_WRITE_ROOTS": str(root),
            }
            with patch.dict(os.environ, env, clear=False):
                first = self.mod.write_text(
                    str(target), "one", overwrite=False
                )
                self.assertTrue(first["ok"])
                self.assertIsNone(first["before"])
                self.assertEqual(
                    target.read_text(encoding="utf-8"), "one"
                )
                old_sha = first["after"]["sha256"]

                conflict = self.mod.write_text(
                    str(target),
                    "two",
                    overwrite=True,
                    expected_sha256="0" * 64,
                )
                self.assertFalse(conflict["ok"])
                self.assertEqual(
                    conflict["error"], "WRITE_CONFLICT"
                )
                self.assertEqual(
                    target.read_text(encoding="utf-8"), "one"
                )

                second = self.mod.write_text(
                    str(target),
                    "two",
                    overwrite=True,
                    expected_sha256=old_sha,
                )
                self.assertTrue(second["ok"])
                self.assertNotEqual(
                    second["before"]["sha256"],
                    second["after"]["sha256"],
                )

    def test_patch_requires_unique_expected_text(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            target = root / "a.txt"
            target.write_text("A A", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "MINI_MCP_READ_ROOTS": str(root),
                    "MINI_MCP_WRITE_ROOTS": str(root),
                },
                clear=False,
            ):
                result = self.mod.patch_text(
                    str(target), "A", "B"
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"], "EXPECTED_TEXT_NOT_UNIQUE"
                )
                self.assertEqual(
                    target.read_text(encoding="utf-8"), "A A"
                )

    def test_search_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            (root / "x.py").write_text(
                "needle\nneedle\n", encoding="utf-8"
            )
            with patch.dict(
                os.environ,
                {"MINI_MCP_READ_ROOTS": str(root)},
                clear=False,
            ):
                result = self.mod.search_text(
                    str(root),
                    "needle",
                    glob="*.py",
                    max_results=1,
                )
                self.assertTrue(result["ok"])
                self.assertEqual(len(result["matches"]), 1)
                self.assertTrue(result["truncated"])

    def test_exec_allowlist_denies_unlisted_binary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            env = {
                "MINI_MCP_READ_ROOTS": str(root),
                "MINI_MCP_EXEC_ALLOW":
                    "definitely-not-python.exe",
            }
            with patch.dict(os.environ, env, clear=False):
                result = self.mod.run_process(
                    sys.executable,
                    ["-c", "print('ok')"],
                    cwd=str(root),
                )
                self.assertFalse(result["ok"])
                self.assertIn(
                    "not allowed", result["error"].lower()
                )

    def test_run_process_returns_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            env = {
                "MINI_MCP_READ_ROOTS": str(root),
                "MINI_MCP_EXEC_ALLOW":
                    Path(sys.executable).name,
            }
            with patch.dict(os.environ, env, clear=False):
                result = self.mod.run_process(
                    sys.executable,
                    ["-c", "print('ok')"],
                    cwd=str(root),
                    timeout_seconds=10,
                )
                self.assertTrue(result["ok"])
                self.assertEqual(result["exit_code"], 0)
                self.assertIn("ok", result["stdout"])
                self.assertEqual(
                    len(result["command_sha256"]), 64
                )


if __name__ == "__main__":
    unittest.main()
