from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def load_config(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件顶层必须是 JSON 对象: {config_path}")
    return data


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_parent(path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def redact_serial(value: str) -> str:
    """Hide the middle of a RIGOL serial in terminal output and logs."""
    parts = value.strip().split(",")
    if len(parts) >= 3:
        serial = parts[2].strip()
        if len(serial) > 6:
            parts[2] = f"{serial[:3]}***{serial[-3:]}"
        elif serial:
            parts[2] = "***"
        return ",".join(parts)
    return re.sub(
        r"(DS[A-Z0-9]{2})[A-Z0-9]{4,}([A-Z0-9]{3})",
        r"\1***\2",
        value,
        flags=re.IGNORECASE,
    )


def parse_float(value: str) -> float | None:
    try:
        number = float(value.strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or abs(number) >= 1e37:
        return None
    return number


def save_json(path: str | Path, data: Any) -> Path:
    output = ensure_parent(path)
    with output.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")
    return output


def setup_logging(log_path: str | Path = "output/logs/rigol.log") -> logging.Logger:
    output = ensure_parent(log_path)
    logger = logging.getLogger("rigol_ds1102ze")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        file_handler = logging.FileHandler(output, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger
