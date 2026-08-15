from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from src.utils import now_iso

from .adapter import DeviceAdapter


def monitor_events(
    adapter: DeviceAdapter,
    *,
    interval_s: float = 3.0,
    count: int | None = None,
) -> Iterator[dict[str, Any]]:
    previous: dict[str, Any] | None = None
    polls = 0
    while count is None or polls < count:
        polls += 1
        try:
            current = adapter.invoke("get_device_status", {})
            snapshot = {
                "online": True,
                "identity": current.get("identity"),
                "channels": current.get("status", {}).get("channels", {}),
            }
        except Exception as exc:
            snapshot = {
                "online": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        event_type = _event_type(previous, snapshot)
        if event_type != "unchanged":
            yield {"type": event_type, "timestamp": now_iso(), "snapshot": snapshot}
        previous = snapshot
        if count is None or polls < count:
            time.sleep(interval_s)


def _event_type(previous: dict[str, Any] | None, current: dict[str, Any]) -> str:
    if previous is None:
        return "online" if current["online"] else "offline"
    if previous["online"] != current["online"]:
        return "online" if current["online"] else "offline"
    if current["online"] and previous.get("channels") != current.get("channels"):
        return "changed"
    return "unchanged"
