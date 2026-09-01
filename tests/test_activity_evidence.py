from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bridge.activity_evidence import ActivityEvidenceCollector


class ActivityEvidenceTests(unittest.TestCase):
    def test_workspace_filter_and_non_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            codex = root / "codex"
            session = codex / "sessions" / "2026" / "08" / "28"
            session.mkdir(parents=True)
            (session / "rollout.jsonl").write_text(
                json.dumps({"timestamp":"2026-08-28T10:00:00Z","type":"tool_result","workspace":str(workspace),"message":"keep"})+"\n"+
                json.dumps({"timestamp":"2026-08-28T10:01:00Z","type":"tool_result","workspace":str(root/"other"),"message":"drop"})+"\n",
                encoding="utf-8",
            )
            agy = root / "agy"
            logdir = agy / "log"
            logdir.mkdir(parents=True)
            (logdir / "cli-test.log").write_text(
                f"workspaceDirs=[{workspace}]\nHandleUserInput called with text: inspect workspace\n",
                encoding="utf-8",
            )
            result = ActivityEvidenceCollector({
                "enabled": True,
                "sources": {
                    "codex": {"root": str(codex), "patterns": ["sessions/**/*.jsonl"]},
                    "antigravity": {"root": str(agy), "pattern": "log/cli-*.log"},
                },
            }).collect(workspace)
            self.assertEqual(result["authority"], "NON_CANONICAL_ACTIVITY_EVIDENCE")
            self.assertEqual(result["evidenceLevel"], "OBSERVED_ACTIVITY_LOG")
            self.assertTrue(result["readOnly"])
            summaries = "\n".join(item["summary"] for item in result["events"])
            self.assertIn("keep", summaries)
            self.assertIn("inspect workspace", summaries)
            self.assertNotIn("drop", summaries)

    def test_disabled(self):
        result = ActivityEvidenceCollector({"enabled": False}).collect(Path.cwd())
        self.assertEqual(result["events"], [])
        self.assertEqual(result["sources"], [])


if __name__ == "__main__":
    unittest.main()
