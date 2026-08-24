from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from bridge.birdeye_request_bridge import BridgeError, RequestBridge, Workspace, redact


class FakeClient:
    def list_dir(self, path):
        return []


class BridgeTests(unittest.TestCase):
    def make_bridge(self, root: Path) -> RequestBridge:
        bridge = object.__new__(RequestBridge)
        bridge.config = {}
        bridge.machine_id = "test-machine"
        bridge.poll_seconds = 45
        bridge.client = FakeClient()
        bridge.workspaces = {
            "workspace": Workspace(
                "workspace",
                root,
                None,
                {"default": [["python", "-c", "print('ok')"]]},
            )
        }
        return bridge

    def request(self, **overrides):
        now = datetime.now(timezone.utc)
        value = {
            "schemaVersion": 1,
            "requestId": "req-1",
            "createdAt": now.isoformat(),
            "expiresAt": (now + timedelta(minutes=5)).isoformat(),
            "workspaceId": "workspace",
            "operation": "workspace_status",
            "mutationAllowed": False,
        }
        value.update(overrides)
        return value

    def test_rejects_mutation_request(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = self.make_bridge(Path(directory))
            with self.assertRaisesRegex(BridgeError, "Mutation requests"):
                bridge._validate_request(self.request(mutationAllowed=True))

    def test_rejects_unknown_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = self.make_bridge(Path(directory))
            with self.assertRaisesRegex(BridgeError, "Operation not allowed"):
                bridge._validate_request(self.request(operation="run_arbitrary_command"))

    def test_rejects_expired_request(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = self.make_bridge(Path(directory))
            expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with self.assertRaisesRegex(BridgeError, "expired"):
                bridge._validate_request(self.request(expiresAt=expired))

    def test_redacts_workspace_and_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.dict(os.environ, {"BIRDEYE_GITHUB_TOKEN": "secret-token"}):
                value = redact(
                    {"path": str(root / "file.js"), "text": "secret-token"},
                    [root],
                )
            self.assertNotIn(str(root), json.dumps(value))
            self.assertNotIn("secret-token", json.dumps(value))

    def test_verdict_boundaries(self):
        self.assertEqual(
            RequestBridge._verdict({"validation": {"verdict": "FAIL"}, "git": {}, "index": {}}),
            "FAIL",
        )
        self.assertEqual(
            RequestBridge._verdict({"git": {"dirty": True}, "index": {"available": True}}),
            "REVIEW",
        )
        self.assertEqual(
            RequestBridge._verdict({"git": {"dirty": False}, "index": {"available": True}}),
            "PASS",
        )

    def test_validation_profile_is_local_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = self.make_bridge(root)
            results = bridge._run_profile(bridge.workspaces["workspace"], "default")
            self.assertEqual(results[0]["exitCode"], 0)
            self.assertIn("ok", results[0]["stdout"])
            with self.assertRaisesRegex(BridgeError, "Unknown validation profile"):
                bridge._run_profile(bridge.workspaces["workspace"], "request-supplied")


if __name__ == "__main__":
    unittest.main()
