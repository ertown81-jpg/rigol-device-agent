from __future__ import annotations

from typing import Any

from .models import ToolResult


LABELS = {
    "FREQUENCY": ("频率", "Hz"), "PERIOD": ("周期", "s"),
    "VPP": ("峰峰值", "V"), "VMAX": ("最大值", "V"),
    "VMIN": ("最小值", "V"), "VAVG": ("平均值", "V"),
    "RMS": ("RMS", "V"), "PDUTY": ("正占空比", "%"),
    "NDUTY": ("负占空比", "%"), "RISE_TIME": ("上升时间", "s"),
    "FALL_TIME": ("下降时间", "s"),
}


def analyze_results(results: list[ToolResult]) -> dict[str, Any]:
    observations: list[str] = []
    warnings: list[str] = []
    measurements: dict[str, Any] = {}
    waveform: dict[str, Any] | None = None

    for result in results:
        if not result.success:
            warnings.append(f"{result.tool} 未完成：{result.error}")
            continue
        data = result.data if isinstance(result.data, dict) else {}
        if result.tool == "get_device_status":
            identity = data.get("identity", {})
            status = data.get("status", {})
            observations.append(
                f"设备在线：{identity.get('manufacturer', '')} {identity.get('model', '')}，固件 {identity.get('firmware', '')}。"
            )
            for name, config in status.get("channels", {}).items():
                observations.append(
                    f"{name} {'已开启' if config.get('enabled') else '已关闭'}，垂直档位 {config.get('scale_v_per_div')} V/div，探头倍率配置 {config.get('probe_ratio')}X。"
                )
            for error in data.get("errors", []):
                warnings.append(f"设备错误队列：{error.get('code')} {error.get('message')}。")
        elif result.tool == "measure":
            measurements.update(data.get("measurements", {}))
        elif result.tool == "capture_waveform":
            waveform = data.get("capture", {})

    for name, value in measurements.items():
        label, unit = LABELS.get(name, (name, ""))
        if value is None:
            warnings.append(f"{label}当前无有效值，通常表示没有满足测量条件的稳定信号，而不是数值为 0。")
        else:
            observations.append(f"{label}：{_format_number(value)} {unit}。")

    if waveform:
        minimum = waveform.get("minimum_v")
        maximum = waveform.get("maximum_v")
        points = waveform.get("points")
        observations.append(f"已取得 {points} 个波形点，电压范围 {minimum} V 至 {maximum} V。")
        if minimum is not None and maximum is not None and measurements.get("VPP") is not None:
            waveform_vpp = float(maximum) - float(minimum)
            measured_vpp = float(measurements["VPP"])
            tolerance = max(0.05, abs(measured_vpp) * 0.25)
            if abs(waveform_vpp - measured_vpp) <= tolerance:
                observations.append("自动测量的峰峰值与采集波形范围基本一致。")
            else:
                warnings.append("自动测量峰峰值与当前屏幕波形范围存在差异，可能是采集时刻、噪声或量程造成。")

    if not observations and not warnings:
        warnings.append("没有足够的数据形成设备结论。")
    conclusion = " ".join(observations + warnings)
    return {"conclusion": conclusion, "observations": observations, "warnings": warnings}


def _format_number(value: Any) -> str:
    number = float(value)
    if number == 0:
        return "0"
    if abs(number) >= 1000 or abs(number) < 0.001:
        return f"{number:.6g}"
    return f"{number:.6f}".rstrip("0").rstrip(".")
