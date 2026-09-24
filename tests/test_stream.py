"""Tests for the line-streaming subprocess runner."""

from __future__ import annotations

import logging

from dgx_hub.process.stream import failure_summary, run_streaming, strip_ansi

logger = logging.getLogger("dgx_hub.test_stream")


def test_streams_lines_strips_ansi_and_merges_stderr() -> None:
    seen: list[str] = []
    result = run_streaming(
        ["bash", "-c", r'echo -e "\033[1;34m[INFO]\033[0m hello"; echo oops >&2; exit 3'],
        logger=logger,
        prefix="x",
        on_line=seen.append,
    )
    assert result.returncode == 3
    assert seen == ["[INFO] hello", "oops"]
    assert result.tail == seen


def test_tail_is_capped() -> None:
    result = run_streaming(
        ["bash", "-c", "for i in $(seq 1 100); do echo line$i; done"],
        logger=logger,
        prefix="x",
        tail_lines=5,
    )
    assert result.tail == [f"line{i}" for i in range(96, 101)]


def test_strip_ansi() -> None:
    assert strip_ansi("\x1b[1;31m[ERR ]\x1b[0m  bad") == "[ERR ]  bad"


def test_failure_summary_prefers_error_lines_with_continuations() -> None:
    tail = [
        "[INFO]  step 1",
        "[ERR ]  Only 69 GiB available now",
        "       Something else is holding memory.",
        "[INFO]  unrelated",
    ]
    assert failure_summary(tail) == (
        "[ERR ]  Only 69 GiB available now\n       Something else is holding memory."
    )


def test_failure_summary_falls_back_to_last_lines() -> None:
    assert failure_summary(["a", "b", "c"], max_lines=2) == (
        "no error message in the output; it ended with:\nb\nc"
    )
    assert failure_summary([]) == "(no output)"
