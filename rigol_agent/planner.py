from __future__ import annotations

import re
from typing import Any

from .models import PlanStep, TaskPlan


MEASUREMENT_WORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("频率", "frequency"), "FREQUENCY"),
    (("周期", "period"), "PERIOD"),
    (("峰峰值", "峰-峰值", "vpp", "peak to peak"), "VPP"),
    (("最大值", "vmax", "maximum"), "VMAX"),
    (("最小值", "vmin", "minimum"), "VMIN"),
    (("平均值", "vavg", "average"), "VAVG"),
    (("有效值", "rms"), "RMS"),
    (("正占空比", "pduty"), "PDUTY"),
    (("负占空比", "nduty"), "NDUTY"),
    (("上升时间", "rise time"), "RISE_TIME"),
    (("下降时间", "fall time"), "FALL_TIME"),
)


def _channel(text: str) -> int:
    match = re.search(r"(?:ch|通道)\s*([12])", text, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def _number_with_unit(text: str, keywords: tuple[str, ...], units: dict[str, float]) -> float | None:
    keyword_pattern = "|".join(re.escape(word) for word in keywords)
    unit_pattern = "|".join(sorted((re.escape(unit) for unit in units), key=len, reverse=True))
    match = re.search(
        rf"(?:{keyword_pattern})[^\d+\-.]*([+-]?\d+(?:\.\d+)?)\s*({unit_pattern})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return float(match.group(1)) * units[match.group(2).lower()]


class RuleBasedPlanner:
    """Small deterministic planner for common Chinese and English lab requests."""

    def plan(self, request: str) -> TaskPlan:
        text = request.strip()
        lowered = text.lower()
        channel = _channel(text)
        steps: list[PlanStep] = []

        measurements = [
            item
            for aliases, item in MEASUREMENT_WORDS
            if any(alias.lower() in lowered for alias in aliases)
        ]
        inspect_signal = bool(
            re.search(
                r"(?:检查|分析|查看)(?:\s*ch[12]|\s*通道\s*[12]|当前|一下|这个|\s)*信号|(?:inspect|analyze)\s+(?:ch[12]\s+)?signal",
                lowered,
                re.IGNORECASE,
            )
        )
        if inspect_signal and not measurements:
            measurements = ["FREQUENCY", "VPP", "RMS"]

        asks_status = any(
            word in lowered
            for word in ("状态", "配置", "是否在线", "设备信息", "识别设备", "status", "configuration", "identify")
        )
        asks_waveform = any(word in lowered for word in ("波形", "csv", "waveform"))
        asks_screen = any(word in lowered for word in ("截图", "屏幕图", "screenshot"))
        if asks_status or inspect_signal or (measurements and (asks_waveform or asks_screen)):
            steps.append(PlanStep("get_device_status", {}, "确认设备在线并读取当前配置"))
        if measurements:
            steps.append(
                PlanStep(
                    "measure",
                    {"channel": channel, "measurements": measurements},
                    f"读取 CH{channel} 所需测量值",
                )
            )
        if asks_waveform:
            mode = "RAW" if any(word in lowered for word in ("raw", "深存储", "完整波形")) else "NORMAL"
            points_match = re.search(r"(?:最多|限制|max(?:imum)?)[^\d]*(\d+)", lowered)
            max_points = int(points_match.group(1)) if points_match else None
            steps.append(
                PlanStep(
                    "capture_waveform",
                    {"channel": channel, "mode": mode, "max_points": max_points},
                    f"保存 CH{channel} {mode} 波形",
                )
            )
        if asks_screen:
            steps.append(PlanStep("capture_screen", {}, "保存示波器当前屏幕"))

        enabled: bool | None = None
        if re.search(r"(?:开启|打开|启用|enable|turn on)[^。；,]*?(?:ch|通道)\s*[12]", lowered, re.IGNORECASE):
            enabled = True
        elif re.search(r"(?:关闭|禁用|disable|turn off)[^。；,]*?(?:ch|通道)\s*[12]", lowered, re.IGNORECASE):
            enabled = False
        if enabled is not None:
            steps.append(
                PlanStep(
                    "set_channel_enabled",
                    {"channel": channel, "enabled": enabled},
                    f"{'开启' if enabled else '关闭'} CH{channel}",
                )
            )

        channel_scale = _number_with_unit(
            lowered,
            ("垂直档位", "通道档位", "vertical scale"),
            {"mv": 1e-3, "v": 1.0, "毫伏": 1e-3, "伏": 1.0},
        )
        if channel_scale is not None:
            steps.append(
                PlanStep(
                    "set_channel_scale",
                    {"channel": channel, "volts_per_div": channel_scale},
                    f"设置 CH{channel} 垂直档位",
                )
            )

        timebase = _number_with_unit(
            lowered,
            ("时基", "水平档位", "timebase"),
            {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "纳秒": 1e-9, "微秒": 1e-6, "毫秒": 1e-3, "秒": 1.0},
        )
        if timebase is not None:
            steps.append(PlanStep("set_timebase_scale", {"seconds_per_div": timebase}, "设置主时基"))

        trigger_level = _number_with_unit(
            lowered,
            ("触发电平", "trigger level"),
            {"mv": 1e-3, "v": 1.0, "毫伏": 1e-3, "伏": 1.0},
        )
        if trigger_level is not None:
            steps.append(PlanStep("set_trigger_level", {"level_v": trigger_level}, "设置边沿触发电平"))

        if re.search(r"(?:单次采集|single acquisition|\bsingle\b)", lowered):
            steps.append(PlanStep("single", {}, "执行一次单次采集"))
        elif re.search(r"(?:停止采集|停止示波器|\bstop\b)", lowered):
            steps.append(PlanStep("stop", {}, "停止采集"))
        elif re.search(r"(?:开始采集|连续采集|运行示波器|\brun\b)", lowered):
            steps.append(PlanStep("run", {}, "开始连续采集"))

        steps = _deduplicate(steps)
        summary = (
            f"计划执行 {len(steps)} 个受控工具步骤"
            if steps
            else "当前请求超出规则规划器的能力边界；不会向设备发送命令"
        )
        return TaskPlan(request=text, steps=steps, summary=summary)


def _deduplicate(steps: list[PlanStep]) -> list[PlanStep]:
    result: list[PlanStep] = []
    seen: set[tuple[str, str]] = set()
    for step in steps:
        key = (step.tool, repr(sorted(step.arguments.items())))
        if key not in seen:
            result.append(step)
            seen.add(key)
    return result
