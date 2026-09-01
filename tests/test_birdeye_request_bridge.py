from __future__ import annotations

import hashlib
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

    @staticmethod
    def init_repo(root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)

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

    def test_workspace_file_state_returns_sha_and_relative_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.init_repo(root)
            target = root / "src" / "demo.txt"
            target.parent.mkdir()
            target.write_text("hello bird-eye\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/demo.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)

            bridge = self.make_bridge(root)
            result = bridge.process(
                self.request(
                    operation="workspace_file_state",
                    scope={"files": ["src/demo.txt"]},
                )
            )
            file_state = result["fileState"]
            self.assertEqual(file_state["workspaceId"], "workspace")
            self.assertEqual(len(file_state["files"]), 1)
            item = file_state["files"][0]
            self.assertEqual(item["path"], "src/demo.txt")
            self.assertEqual(item["state"], "PRESENT")
            self.assertEqual(
                item["sha256"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )
            self.assertEqual(item["gitStatus"], "CLEAN")
            self.assertIsNone(item["indexed"])
            self.assertNotIn(str(root), json.dumps(result))

    def test_workspace_file_state_reports_modified_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.init_repo(root)
            target = root / "tracked.txt"
            target.write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)
            target.write_text("two\n", encoding="utf-8")

            bridge = self.make_bridge(root)
            state = bridge._workspace_file_state(
                bridge.workspaces["workspace"],
                {"files": ["tracked.txt", "missing.txt"]},
            )
            by_path = {item["path"]: item for item in state["files"]}
            self.assertIn("M", by_path["tracked.txt"]["gitStatus"])
            self.assertEqual(by_path["missing.txt"]["state"], "MISSING")
            self.assertIsNone(by_path["missing.txt"]["sha256"])

    def test_workspace_file_state_rejects_traversal_absolute_and_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.init_repo(root)
            (root / "folder").mkdir()
            bridge = self.make_bridge(root)
            workspace = bridge.workspaces["workspace"]
            with self.assertRaisesRegex(BridgeError, "outside workspace"):
                bridge._workspace_file_state(workspace, {"files": ["../escape.txt"]})
            with self.assertRaisesRegex(BridgeError, "workspace-relative"):
                bridge._workspace_file_state(workspace, {"files": [str(root / "file.txt")]})
            with self.assertRaisesRegex(BridgeError, "not a regular file"):
                bridge._workspace_file_state(workspace, {"files": ["folder"]})

    def test_workspace_file_state_requires_bounded_file_list(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bridge = self.make_bridge(root)
            workspace = bridge.workspaces["workspace"]
            with self.assertRaisesRegex(BridgeError, "requires scope.files"):
                bridge._workspace_file_state(workspace, {})
            too_many = [f"file-{index}.txt" for index in range(101)]
            with self.assertRaisesRegex(BridgeError, "at most 100 files"):
                bridge._workspace_file_state(workspace, {"files": too_many})


if __name__ == "__main__":
    unittest.main()
