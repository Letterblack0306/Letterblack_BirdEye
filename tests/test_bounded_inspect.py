from __future__ import annotations

import hashlib

import pytest

import agent


def _context(root) -> agent.Context:
    return agent.Context(
        config={"max_file_bytes": 5_000_000},
        governance={"allowed_read_paths": ["."]},
        roots=(agent.KnowledgeRoot("test", root, "workspace"),),
    )


def _file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = root / "sample.txt"
    data = "one\ntwo\nthree\nfour\n"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(data)
    return root, path, data.encode("utf-8")


def test_inspect_full_file_preserves_path_and_sha(tmp_path):
    root, _, data = _file(tmp_path)

    result = agent.inspect_file(_context(root), "test/sample.txt")

    assert result["content"] == data.decode("utf-8")
    assert result["path"] == "test/sample.txt"
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert "line_range" not in result


@pytest.mark.parametrize(
    ("start_line", "end_line", "content", "line_range"),
    [
        (1, 1, "one\n", {"start": 1, "end": 1}),
        (2, 3, "two\nthree\n", {"start": 2, "end": 3}),
        (None, 2, "one\ntwo\n", {"start": 1, "end": 2}),
        (3, None, "three\nfour\n", {"start": 3, "end": 4}),
        (2, 99, "two\nthree\nfour\n", {"start": 2, "end": 4}),
    ],
)
def test_inspect_ranges_are_one_based_inclusive_and_bounded(
    tmp_path, start_line, end_line, content, line_range
):
    root, _, data = _file(tmp_path)

    result = agent.inspect_file(
        _context(root), "test/sample.txt", start_line=start_line, end_line=end_line
    )

    assert result["content"] == content
    assert result["line_range"] == line_range
    assert result["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize(
    ("start_line", "end_line"),
    [(0, None), (-1, None), (None, 0), (None, -1), (3, 2), ("1", None), (None, "2"), (True, None)],
)
def test_inspect_rejects_invalid_range_inputs(tmp_path, start_line, end_line):
    root, _, _ = _file(tmp_path)

    with pytest.raises(ValueError):
        agent.inspect_file(
            _context(root), "test/sample.txt", start_line=start_line, end_line=end_line
        )


def test_inspect_rejects_start_beyond_file_length(tmp_path):
    root, _, _ = _file(tmp_path)

    with pytest.raises(ValueError, match="exceeds file length"):
        agent.inspect_file(_context(root), "test/sample.txt", start_line=5)


@pytest.mark.parametrize("value", ["test/../outside.txt", "test/missing/sample.txt"])
def test_inspect_preserves_path_confinement(tmp_path, value):
    root, _, _ = _file(tmp_path)
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")

    with pytest.raises((agent.GovernanceError, FileNotFoundError)):
        agent.inspect_file(_context(root), value)
