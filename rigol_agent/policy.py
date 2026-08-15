from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

from .models import RiskLevel, ToolSpec


class PolicyViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionPolicy:
    allow_changes: bool = False
    allow_guarded: bool = False
    device_label: str = "DS1102Z-E"
    argument_validator: Callable[[str, dict[str, Any]], None] | None = None

    def authorize(self, spec: ToolSpec, arguments: dict[str, Any]) -> None:
        if spec.risk is RiskLevel.PROHIBITED:
            raise PolicyViolation(f"工具 {spec.name} 被安全策略永久禁止")
        if spec.risk is RiskLevel.GUARDED and not self.allow_guarded:
            raise PolicyViolation(
                f"工具 {spec.name} 需要 guarded 权限；当前任务没有授权"
            )
        if spec.risk is RiskLevel.REVERSIBLE and not self.allow_changes:
            raise PolicyViolation(
                f"工具 {spec.name} 会修改 {self.device_label} 状态；请显式使用 --allow-changes"
            )
        if (
            spec.name == "capture_waveform"
            and str(arguments.get("mode", "NORMAL")).upper() == "RAW"
            and not self.allow_guarded
        ):
            raise PolicyViolation(
                "RAW 深存储读取会临时停止采集；请显式使用 --allow-guarded"
            )
        validator = self.argument_validator or validate_arguments
        validator(spec.name, arguments)


def validate_arguments(tool: str, arguments: dict[str, Any]) -> None:
    channel = arguments.get("channel")
    if channel is not None and channel not in (1, 2):
        raise PolicyViolation("DS1102Z-E 只允许使用通道 1 或 2")

    if tool == "set_channel_scale":
        value = float(arguments["volts_per_div"])
        _require_finite(value)
        if not 1e-3 <= value <= 10:
            raise PolicyViolation("垂直档位必须位于 1 mV/div 到 10 V/div")
    elif tool == "set_timebase_scale":
        value = float(arguments["seconds_per_div"])
        _require_finite(value)
        if not 5e-9 <= value <= 50:
            raise PolicyViolation("时基必须位于 5 ns/div 到 50 s/div")
    elif tool == "set_trigger_level":
        value = float(arguments["level_v"])
        _require_finite(value)
        if not -100 <= value <= 100:
            raise PolicyViolation("Agent 暴露的触发电平范围限制为 -100 V 到 100 V")
    elif tool == "capture_waveform":
        mode = str(arguments.get("mode", "NORMAL")).upper()
        if mode not in {"NORMAL", "RAW"}:
            raise PolicyViolation("波形模式只能是 NORMAL 或 RAW")
        points = arguments.get("max_points")
        if points is not None and not 1 <= int(points) <= 10_000_000:
            raise PolicyViolation("波形点数必须位于 1 到 10000000")


def _require_finite(value: float) -> None:
    if not math.isfinite(value):
        raise PolicyViolation("设备参数必须是有限数值，禁止 NaN 或 Infinity")
