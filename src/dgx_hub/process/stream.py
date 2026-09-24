"""Run a subprocess while streaming its combined output line by line."""

from __future__ import annotations

import logging
import re
import subprocess
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ERROR_MARKERS = ("[ERR", "ERROR", "Error:", "error:", "fatal:")

DEFAULT_TAIL_LINES = 40


@dataclass(frozen=True)
class StreamResult:
    returncode: int
    tail: list[str] = field(default_factory=list)
    """The last `tail_lines` output lines (ANSI-stripped, stdout+stderr merged)."""

    @property
    def output(self) -> str:
        return "\n".join(self.tail)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def run_streaming(
    argv: Sequence[str],
    *,
    logger: logging.Logger,
    prefix: str,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    on_line: Callable[[str], None] | None = None,
    tail_lines: int = DEFAULT_TAIL_LINES,
) -> StreamResult:
    """Run `argv`, logging each output line at INFO as `"<prefix>: <line>"` as
    it arrives (instead of only after exit), and calling `on_line` for it.

    stderr is merged into stdout: several repo scripts print their own
    `[ERR ]` lines to stdout, so treating the two streams separately loses
    the actual failure reason.
    """
    tail: deque[str] = deque(maxlen=tail_lines)
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    try:
        for raw in process.stdout:
            line = strip_ansi(raw.rstrip("\n")).rstrip()
            if not line:
                continue
            tail.append(line)
            logger.info("%s: %s", prefix, line)
            if on_line is not None:
                on_line(line)
    finally:
        process.stdout.close()
        returncode = process.wait()
    return StreamResult(returncode=returncode, tail=list(tail))


def failure_summary(tail: Sequence[str], max_lines: int = 5) -> str:
    """A short, human-readable reason for a failed command, from its output
    tail: the error-marked lines if there are any (plus their indented
    continuation lines), else the last few lines.
    """
    lines = [line for line in tail if line.strip()]
    if not lines:
        return "(no output)"

    picked: list[str] = []
    in_error = False
    for line in lines:
        if any(marker in line for marker in _ERROR_MARKERS):
            picked.append(line)
            in_error = True
        elif in_error and line[:1].isspace():
            picked.append(line)
        else:
            in_error = False
    if picked:
        return "\n".join(picked[-max_lines:])
    return "no error message in the output; it ended with:\n" + "\n".join(lines[-max_lines:])
