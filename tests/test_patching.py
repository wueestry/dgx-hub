"""Tests for applying manifest [[patch]] entries to a cloned repo."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from dgx_hub.plugins.manifest import PatchSpec
from dgx_hub.plugins.patching import PatchError, apply_patches

logger = logging.getLogger("dgx_hub.test_patching")


def _apply(repo: Path, *patches: PatchSpec) -> None:
    apply_patches(repo, list(patches), logger, "x")


def test_find_replace_applies_once_then_is_a_noop(tmp_path: Path) -> None:
    script = tmp_path / "start.sh"
    script.write_text("a\nif [[ ! -f x ]]; then\nb\n")
    script.chmod(0o755)
    patch = PatchSpec(file="start.sh", find="if [[ ! -f x ]]; then", replace="if true; then")

    _apply(tmp_path, patch)
    assert script.read_text() == "a\nif true; then\nb\n"
    assert script.stat().st_mode & 0o777 == 0o755

    _apply(tmp_path, patch)
    assert script.read_text() == "a\nif true; then\nb\n"


def test_find_replace_raises_when_upstream_changed(tmp_path: Path) -> None:
    (tmp_path / "start.sh").write_text("something else entirely\n")
    patch = PatchSpec(file="start.sh", find="old", replace="new", description="fix it")
    with pytest.raises(PatchError, match=r"fix it.*upstream repo changed"):
        _apply(tmp_path, patch)


def test_rejects_paths_outside_the_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.txt").write_text("old\n")
    with pytest.raises(PatchError, match="escapes"):
        _apply(repo, PatchSpec(file="../outside.txt", find="old", replace="new"))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(PatchError, match="does not exist"):
        _apply(tmp_path, PatchSpec(file="nope.sh", find="a", replace="b"))


DIFF = """\
--- a/run.sh
+++ b/run.sh
@@ -1,2 +1,2 @@
 echo start
-echo old
+echo new
"""


def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "run.sh").write_text("echo start\necho old\n")
    return path


def test_diff_applies_then_is_detected_as_already_applied(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    patch = PatchSpec(file="run.sh", diff=DIFF)

    _apply(repo, patch)
    assert (repo / "run.sh").read_text() == "echo start\necho new\n"

    _apply(repo, patch)
    assert (repo / "run.sh").read_text() == "echo start\necho new\n"


def test_diff_raises_on_conflict(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "run.sh").write_text("echo start\necho changed upstream\n")
    with pytest.raises(PatchError, match="no longer applies"):
        _apply(repo, PatchSpec(file="run.sh", diff=DIFF))
