from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_server as bird


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "http_mcp_server.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "birdeye_http_transport_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BirdEyeHttpTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_canonical_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BIRDEYE_HTTP_HOST", None)
            os.environ.pop("BIRDEYE_HTTP_PORT", None)
            host, port = self.mod._configured_host_port()
        self.assertEqual(host, "127.0.0.1")
        self.assertEqual(port, 8766)
        self.assertEqual(self.mod.MCP_PATH, "/mcp")
        self.assertEqual(self.mod.SERVER_NAME, "letterblack-birdeye")

    def test_transport_reuses_full_birdeye_registry(self):
        expected = [tool["name"] for tool in bird._TOOL_DEFINITIONS]
        self.assertEqual(self.mod._tool_names(), expected)
        self.assertIn("birdeye_status", expected)
        self.assertIn("birdeye_roots", expected)
        self.assertIn("memory_recall", expected)
        self.assertIn("knowledge_route", expected)
        self.assertIn("workspace_identity", expected)
        self.assertIn("workspace_run", expected)
        self.assertIn("workspace_run_sequence", expected)

    def test_non_loopback_bind_is_rejected(self):
        with patch.dict(
            os.environ,
            {"BIRDEYE_HTTP_HOST": "0.0.0.0", "BIRDEYE_HTTP_PORT": "8766"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "non-loopback"):
                self.mod._configured_host_port()

    def test_invalid_port_is_rejected(self):
        with patch.dict(
            os.environ,
            {"BIRDEYE_HTTP_HOST": "127.0.0.1", "BIRDEYE_HTTP_PORT": "70000"},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid BirdEye HTTP port"):
                self.mod._configured_host_port()

    def test_tool_calls_keep_existing_authority_path(self):
        result = bird.invoke("definitely-not-a-birdeye-tool", {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown tool")


if __name__ == "__main__":
    unittest.main()
