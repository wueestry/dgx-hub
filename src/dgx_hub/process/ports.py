"""Port allocation for bridge-networked (loopback-remap) plugins."""

from __future__ import annotations

import socket

DEFAULT_RANGE = range(8888, 9000)


def _is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def allocate_port(
    claimed_ports: set[int],
    preferred: int | None = None,
    port_range: range = DEFAULT_RANGE,
) -> int:
    """Return a free port not already claimed by another dgx-hub-managed model."""
    candidates: list[int] = []
    if preferred is not None:
        candidates.append(preferred)
    candidates += [p for p in port_range if p != preferred]

    for port in candidates:
        if port in claimed_ports:
            continue
        if _is_free(port):
            return port
    raise RuntimeError(f"no free port available in range {port_range}")
