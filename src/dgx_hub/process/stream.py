"""Run a subprocess while streaming its combined output line by line."""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import threading
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
    timeout: float = 3600.0,
    cancel_event: threading.Event | None = None,
    secrets: Sequence[str] = (),
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
        start_new_session=True,
    )
    done = threading.Event()
    aborted: list[str] = []

    def watchdog() -> None:
        import time

        deadline = time.monotonic() + timeout
        while not done.wait(0.05):
            if (cancel_event and cancel_event.is_set()) or time.monotonic() >= deadline:
                reason = "cancelled" if cancel_event and cancel_event.is_set() else "timed out"
                aborted.append(reason)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    if not done.wait(2):
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                return

    watcher = threading.Thread(target=watchdog, daemon=True)
    watcher.start()
    assert process.stdout is not None
    try:
        for raw in process.stdout:
            line = strip_ansi(raw.rstrip("\n")).rstrip()
            for secret in secrets:
                if secret:
                    line = line.replace(secret, "<redacted>")
            if not line:
                continue
            tail.append(line)
            logger.info("%s: %s", prefix, line)
            if on_line is not None:
                on_line(line)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        raise
    finally:
        process.stdout.close()
        returncode = process.wait()
        done.set()
        watcher.join()
    if aborted:
        tail.append(f"Command {aborted[0]} (deadline: {timeout:g}s)")
        returncode = returncode or 1
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
