from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import Context, KnowledgeRoot
import workspace_identity as identity


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _context(path: Path) -> Context:
    return Context(
        config={},
        governance={},
        roots=(KnowledgeRoot("test-workspace", path.resolve(), "workspace"),),
    )


class WorkspaceIdentityTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "birdeye-test@example.invalid")
        _git(repo, "config", "user.name", "BirdEye Test")
        (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", "initial")
        return repo

    def test_clean_repository_is_bound_to_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            expected_head = _git(repo, "rev-parse", "HEAD")
            with patch.object(identity.Context, "load", return_value=_context(repo)):
                result = identity.workspace_identity()
                revision = identity.revision_status()

            self.assertTrue(result["ok"])
            self.assertTrue(result["git"]["is_repository"])
            self.assertEqual(expected_head, result["git"]["head_sha"])
            self.assertEqual(expected_head, result["evidence_binding"]["head_sha"])
            self.assertFalse(result["git"]["dirty"])
            self.assertFalse(revision["git"]["dirty"])
            self.assertEqual([], revision["git"]["changed_paths"])
            self.assertEqual("unverified", revision["path_states"]["runtime_active"])
            self.assertEqual("unverified", revision["path_states"]["validated"])

    def test_staged_unstaged_and_untracked_paths_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
            _git(repo, "add", "staged.txt")
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("new\n", encoding="utf-8")

            with patch.object(identity.Context, "load", return_value=_context(repo)):
                result = identity.revision_status()

            self.assertTrue(result["git"]["dirty"])
            self.assertIn("staged.txt", result["git"]["staged_paths"])
            self.assertIn("tracked.txt", result["git"]["unstaged_paths"])
            self.assertIn("untracked.txt", result["git"]["untracked_paths"])
            self.assertEqual(3, result["git"]["changed_path_count"])

    def test_detached_head_is_reported_without_inventing_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            head = _git(repo, "rev-parse", "HEAD")
            _git(repo, "checkout", "--detach", head)

            with patch.object(identity.Context, "load", return_value=_context(repo)):
                result = identity.revision_status()

            self.assertTrue(result["git"]["detached_head"])
            self.assertIsNone(result["git"]["branch"])
            self.assertEqual(head, result["git"]["head_sha"])

    def test_non_git_workspace_is_explicit_not_failure_or_success_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plain"
            root.mkdir()
            with patch.object(identity.Context, "load", return_value=_context(root)):
                workspace = identity.workspace_identity()
                revision = identity.revision_status()

            self.assertTrue(workspace["ok"])
            self.assertFalse(workspace["git"]["is_repository"])
            self.assertTrue(revision["ok"])
            self.assertFalse(revision["git"]["is_repository"])


if __name__ == "__main__":
    unittest.main()
