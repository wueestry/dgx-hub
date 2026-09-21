"""Tests for process/ports.py port allocation — no real Docker/GPU required."""

from __future__ import annotations

import socket

import pytest

from dgx_hub.process import ports


def test_prefers_preferred_port_when_free() -> None:
    port = ports.allocate_port(
        claimed_ports=set(), preferred=18881, port_range=range(18800, 18900)
    )
    assert port == 18881


def test_skips_claimed_ports() -> None:
    port = ports.allocate_port(
        claimed_ports={18881}, preferred=18881, port_range=range(18800, 18900)
    )
    assert port != 18881
    assert 18800 <= port < 18900


def test_skips_actually_bound_port() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 18882))
    sock.listen(1)
    try:
        port = ports.allocate_port(
            claimed_ports=set(), preferred=18882, port_range=range(18800, 18900)
        )
        assert port != 18882
    finally:
        sock.close()


def test_raises_when_range_exhausted() -> None:
    with pytest.raises(RuntimeError):
        ports.allocate_port(
            claimed_ports=set(range(18800, 18810)),
            preferred=None,
            port_range=range(18800, 18810),
        )
