"""Apply a manifest's [[patch]] entries to the cloned plugin repo.

Every patch is idempotent: re-running provisioning on an already-patched
clone detects that and skips it, while a patch that no longer matches (the
upstream repo changed underneath it) fails loudly instead of silently
launching unpatched code.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from dgx_hub.plugins.manifest import PatchSpec


class PatchError(RuntimeError):
    """Raised when a [[patch]] entry can't be applied."""


def apply_patches(
    repo_dir: Path, patches: Sequence[PatchSpec], logger: logging.Logger, name: str
) -> None:
    for index, patch in enumerate(patches):
        label = patch.description or f"patch #{index + 1} ({patch.file})"
        target = _resolve_target(repo_dir, patch.file, label)
        if patch.diff is not None:
            outcome = _apply_diff(repo_dir, patch.diff, label)
        else:
            assert patch.find is not None and patch.replace is not None
            outcome = _apply_find_replace(target, patch.find, patch.replace, label)
        logger.info("%s: [[patch]] %s: %s", name, label, outcome)


def _resolve_target(repo_dir: Path, file: str, label: str) -> Path:
    root = repo_dir.resolve()
    target = (root / file).resolve()
    if not target.is_relative_to(root):
        raise PatchError(f"{label}: file {file!r} escapes the repo directory")
    if not target.is_file():
        raise PatchError(f"{label}: file {file!r} does not exist in {repo_dir}")
    return target


def _apply_find_replace(target: Path, find: str, replace: str, label: str) -> str:
    content = target.read_text()
    if find in content:
        _atomic_write(target, content.replace(find, replace))
        return "applied"
    if replace in content:
        return "already applied"
    raise PatchError(
        f"{label}: neither the `find` text nor its `replace` text occurs in "
        f"{target.name} -- the upstream repo changed; update the patch"
    )


def _atomic_write(target: Path, content: str) -> None:
    mode = target.stat().st_mode
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _apply_diff(repo_dir: Path, diff: str, label: str) -> str:
    text = diff if diff.endswith("\n") else diff + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as handle:
        handle.write(text)
        patch_file = handle.name
    try:
        if (repo_dir / ".git").exists():
            return _apply_with_git(repo_dir, patch_file, label)
        return _apply_with_patch(repo_dir, patch_file, label)
    finally:
        Path(patch_file).unlink(missing_ok=True)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def _apply_with_git(repo_dir: Path, patch_file: str, label: str) -> str:
    check = _run(["git", "apply", "--check", patch_file], repo_dir)
    if check.returncode == 0:
        result = _run(["git", "apply", patch_file], repo_dir)
        if result.returncode != 0:
            raise PatchError(f"{label}: git apply failed: {result.stderr.strip()}")
        return "applied"
    if _run(["git", "apply", "--reverse", "--check", patch_file], repo_dir).returncode == 0:
        return "already applied"
    raise PatchError(
        f"{label}: diff no longer applies -- the upstream repo changed; update the "
        f"patch: {check.stderr.strip()}"
    )


def _apply_with_patch(repo_dir: Path, patch_file: str, label: str) -> str:
    base = ["patch", "-p1", "--batch", "--silent", "-i", patch_file]
    if _run([*base, "--forward", "--dry-run"], repo_dir).returncode == 0:
        result = _run([*base, "--forward"], repo_dir)
        if result.returncode != 0:
            raise PatchError(f"{label}: patch failed: {result.stdout.strip()}")
        return "applied"
    if _run([*base, "--reverse", "--dry-run"], repo_dir).returncode == 0:
        return "already applied"
    raise PatchError(
        f"{label}: diff no longer applies -- the upstream repo changed; update the patch"
    )
