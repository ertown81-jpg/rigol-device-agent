from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.utils import now_iso


class ExperimentMemory:
    """Small append-only memory of evidence summaries, never raw model instructions."""

    def __init__(self, path: str | Path = "output/agent/experiment_memory.jsonl") -> None:
        self.path = Path(path)

    def recall(self, *, channel: int, limit: int = 3) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if item.get("channel") != channel:
                continue
            records.append(_public_record(item))
            if len(records) >= max(0, limit):
                break
        return records

    def record(self, record: dict[str, Any]) -> dict[str, Any]:
        safe = {
            "recorded_at": now_iso(),
            "schema_version": 1,
            "session_id": str(record.get("session_id") or ""),
            "channel": int(record.get("channel") or 1),
            "request": str(record.get("request") or "")[:500],
            "final_hypothesis": record.get("final_hypothesis") or {},
            "quality": record.get("quality") or {},
            "execution_success": bool(record.get("execution_success")),
            "scientific_success": bool(record.get("scientific_success")),
            "settings_restored": bool(record.get("settings_restored")),
            "rounds": int(record.get("rounds") or 0),
            "stopping_reason": str(record.get("stopping_reason") or ""),
            "evidence_fingerprint": record.get("evidence_fingerprint") or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False, default=str) + "\n")
        return _public_record(safe)


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return only bounded factual summaries suitable for future planner context."""
    return {
        key: record.get(key)
        for key in (
            "recorded_at",
            "schema_version",
            "session_id",
            "channel",
            "request",
            "final_hypothesis",
            "quality",
            "execution_success",
            "scientific_success",
            "settings_restored",
            "rounds",
            "stopping_reason",
            "evidence_fingerprint",
        )
    }
