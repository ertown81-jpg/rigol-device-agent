from __future__ import annotations

import copy
import json
import math
import uuid
from pathlib import Path
from typing import Any

from src.connection import make_lan_resource
from src.rigol_scope import RigolDS1102ZE
from src.utils import load_config, redact_serial, save_json, setup_logging, timestamp_for_filename
from src.waveform import save_csv

from .reporting import save_waveform_svg
from .device_packs.base import DeviceAdapter


class RigolDeviceAdapter:
    """Persistent, reconnecting adapter around the verified PyVISA driver."""

    def __init__(self, config_path: str | Path = "config.json") -> None:
        config = load_config(config_path)
        resource = str(config.get("resource") or "")
        if not resource and config.get("ip"):
            resource = make_lan_resource(str(config["ip"]))
        if not resource:
            raise ValueError("config.json 中必须填写 resource 或 ip")
        self.scope = RigolDS1102ZE(
            resource,
            backend=config.get("backend") or None,
            timeout_ms=int(config.get("timeout_ms", 10_000)),
            query_delay_s=float(config.get("query_delay_s", 0.05)),
            logger=setup_logging("output/logs/rigol-agent.log"),
        )
        self._identity_verified = False

    def _ensure_connected(self) -> None:
        if not self.scope.connection.connected:
            self.scope.connect()
            self._identity_verified = False
        if not self._identity_verified:
            identity = self.scope.identify()
            if identity.get("manufacturer") != "RIGOL TECHNOLOGIES" or identity.get("model") != "DS1102Z-E":
                self.scope.close()
                raise RuntimeError(
                    f"设备身份与 rigol_ds1102ze 能力包不匹配: "
                    f"{identity.get('manufacturer', '')} {identity.get('model', '')}"
                )
            self._identity_verified = True

    def _call(self, action: Any) -> Any:
        try:
            self._ensure_connected()
            return action()
        except Exception:
            self.scope.close()
            self._identity_verified = False
            raise

    def invoke(self, tool: str, arguments: dict[str, Any]) -> Any:
        handlers = {
            "get_device_status": self._get_device_status,
            "measure": self._measure,
            "capture_waveform": self._capture_waveform,
            "capture_screen": self._capture_screen,
            "set_channel_enabled": self._set_channel_enabled,
            "set_channel_scale": self._set_channel_scale,
            "set_timebase_scale": self._set_timebase_scale,
            "set_trigger_level": self._set_trigger_level,
            "run": lambda _: self._write_action("run"),
            "stop": lambda _: self._write_action("stop"),
            "single": lambda _: self._write_action("single"),
        }
        if tool not in handlers:
            raise KeyError(f"未知设备工具: {tool}")
        return self._call(lambda: handlers[tool](arguments))

    def _public_identity(self) -> dict[str, Any]:
        identity = self.scope.identify()
        identity["serial"] = redact_serial(identity["raw"]).split(",")[2]
        identity["raw"] = redact_serial(identity["raw"])
        return identity

    def _get_device_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "online": True,
            "identity": self._public_identity(),
            "status": self.scope.get_status(),
            "errors": self.scope.get_errors(),
        }

    def _measure(self, arguments: dict[str, Any]) -> dict[str, Any]:
        channel = int(arguments["channel"])
        measurements = [str(item) for item in arguments["measurements"]]
        return {
            "channel": channel,
            "measurements": {
                item: self.scope.measure(channel, item) for item in measurements
            },
            "errors": self.scope.get_errors(),
        }

    def _capture_waveform(self, arguments: dict[str, Any]) -> dict[str, Any]:
        channel = int(arguments["channel"])
        mode = str(arguments.get("mode", "NORMAL")).upper()
        max_points = arguments.get("max_points")
        capture = self.scope.capture_waveform(
            channel,
            mode=mode,
            max_points=int(max_points) if max_points is not None else None,
        )
        stamp = f"{timestamp_for_filename()}_{uuid.uuid4().hex[:6]}"
        csv_path = Path(f"output/agent/waveform_ch{channel}_{stamp}.csv")
        metadata_path = csv_path.with_suffix(".json")
        plot_path = csv_path.with_suffix(".svg")
        save_csv(capture, csv_path)
        save_waveform_svg(capture, plot_path)
        metadata = {
            "identity": self._public_identity(),
            "capture": capture.summary(),
            "csv_path": str(csv_path),
            "plot_path": str(plot_path),
            "errors": self.scope.get_errors(),
        }
        save_json(metadata_path, metadata)
        metadata["metadata_path"] = str(metadata_path)
        return metadata

    def _capture_screen(self, _: dict[str, Any]) -> dict[str, Any]:
        output = Path(f"output/agent/screen_{timestamp_for_filename()}_{uuid.uuid4().hex[:6]}.png")
        saved = self.scope.capture_screen(output)
        return {
            "output": str(saved),
            "bytes": saved.stat().st_size,
            "errors": self.scope.get_errors(),
        }

    def _set_channel_enabled(self, arguments: dict[str, Any]) -> dict[str, Any]:
        channel = int(arguments["channel"])
        enabled = bool(arguments["enabled"])
        before = self.scope.get_channel_config(channel)["enabled"]
        self.scope.set_channel_enabled(channel, enabled)
        after = self.scope.get_channel_config(channel)["enabled"]
        return {"channel": channel, "before": before, "after": after}

    def _set_channel_scale(self, arguments: dict[str, Any]) -> dict[str, Any]:
        channel = int(arguments["channel"])
        value = float(arguments["volts_per_div"])
        before = self.scope.get_channel_config(channel)["scale_v_per_div"]
        self.scope.set_channel_scale(channel, value)
        after = self.scope.get_channel_config(channel)["scale_v_per_div"]
        return {"channel": channel, "before": before, "after": after, "unit": "V/div"}

    def _set_timebase_scale(self, arguments: dict[str, Any]) -> dict[str, Any]:
        value = float(arguments["seconds_per_div"])
        before = self.scope.get_status()["timebase"]["scale_s_per_div"]
        self.scope.set_timebase_scale(value)
        after = self.scope.get_status()["timebase"]["scale_s_per_div"]
        return {"before": before, "after": after, "unit": "s/div"}

    def _set_trigger_level(self, arguments: dict[str, Any]) -> dict[str, Any]:
        value = float(arguments["level_v"])
        status = self.scope.get_status()
        before = status["trigger"].get("level_v")
        self.scope.set_trigger_level(value)
        after = self.scope.get_status()["trigger"].get("level_v")
        return {"before": before, "after": after, "unit": "V"}

    def _write_action(self, action: str) -> dict[str, Any]:
        before = self.scope.query(":TRIG:STAT?")
        getattr(self.scope, action)()
        after = self.scope.query(":TRIG:STAT?")
        return {"before": before, "after": after, "action": action}

    def close(self) -> None:
        self.scope.close()
        self._identity_verified = False


class SimulatedRigolAdapter:
    """Deterministic signal laboratory used for development and repeatable evals."""

    SCENARIOS = ("sine", "low_frequency", "high_frequency", "dc", "noise", "step", "clipped", "low_resolution")

    def __init__(self, scenario: str = "sine", output_dir: str | Path = "output/agent/simulated") -> None:
        if scenario not in self.SCENARIOS:
            raise ValueError(f"未知模拟信号场景: {scenario}")
        self.scenario = scenario
        self.output_dir = Path(output_dir)
        self.capture_count = 0
        self.state: dict[str, Any] = {
            "online": True,
            "identity": {
                "manufacturer": "RIGOL TECHNOLOGIES",
                "model": "DS1102Z-E",
                "serial": "SIM***001",
                "firmware": "SIMULATED",
            },
            "status": {
                "channels": {
                    "CH1": {"enabled": True, "scale_v_per_div": 2.0, "offset_v": 0.0, "coupling": "DC", "probe_ratio": 1.0, "bandwidth_limit": "OFF"},
                    "CH2": {"enabled": False, "scale_v_per_div": 1.0, "offset_v": 0.0, "coupling": "DC", "probe_ratio": 10.0, "bandwidth_limit": "OFF"},
                },
                "timebase": {"scale_s_per_div": 0.001, "offset_s": 0.0},
                "acquisition": {"sample_rate_sa_s": 1e9, "memory_depth_points": 1200},
                "trigger": {"status": "AUTO", "mode": "EDGE", "level_v": 0.0},
            },
        }

    def invoke(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "get_device_status":
            return copy.deepcopy({**self.state, "errors": []})
        if tool == "measure":
            _, samples, quantization = self._samples()
            minimum = min(samples)
            maximum = max(samples)
            frequency = {
                "sine": 1000.0,
                "low_frequency": 2.0,
                "high_frequency": 10_000_000.0,
                "clipped": 1000.0,
            }.get(self.scenario)
            values = {
                "FREQUENCY": frequency,
                "PERIOD": 1.0 / frequency if frequency else None,
                "VPP": maximum - minimum,
                "RMS": math.sqrt(sum(value * value for value in samples) / len(samples)),
                "VMAX": maximum,
                "VMIN": minimum,
                "VAVG": sum(samples) / len(samples),
                "PDUTY": 50.0 if frequency else None,
                "NDUTY": 50.0 if frequency else None,
                "RISE_TIME": 0.00002 if frequency else None,
                "FALL_TIME": 0.00002 if frequency else None,
            }
            return {
                "channel": arguments["channel"],
                "measurements": {name: values.get(name) for name in arguments["measurements"]},
                "simulated_scenario": self.scenario,
                "quantization_v": quantization,
                "errors": [],
            }
        if tool == "capture_waveform":
            times, samples, quantization = self._samples()
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output = self.output_dir / f"{self.scenario}_{self.capture_count:02d}.csv"
            output.parent.mkdir(parents=True, exist_ok=True)
            rows = ["time_s,voltage_v,channel"]
            rows.extend(f"{time_value:.12g},{voltage:.12g},CH{arguments['channel']}" for time_value, voltage in zip(times, samples))
            output.write_text("\n".join(rows) + "\n", encoding="utf-8")
            self.capture_count += 1
            return {
                "capture": {
                    "channel": arguments["channel"],
                    "mode": arguments.get("mode", "NORMAL"),
                    "points": len(samples),
                    "preamble": {"x_increment": times[1] - times[0], "y_increment": quantization},
                    "simulated_scenario": self.scenario,
                },
                "csv_path": str(output),
                "errors": [],
            }
        if tool == "capture_screen":
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output = self.output_dir / f"{self.scenario}_screen.png"
            if not output.exists():
                output.write_bytes(b"SIMULATED SCREEN - NOT REAL DEVICE EVIDENCE")
            return {"output": str(output), "bytes": output.stat().st_size, "simulated": True}
        if tool == "set_channel_enabled":
            key = f"CH{arguments['channel']}"
            before = self.state["status"]["channels"][key]["enabled"]
            self.state["status"]["channels"][key]["enabled"] = arguments["enabled"]
            return {"channel": arguments["channel"], "before": before, "after": arguments["enabled"]}
        if tool == "set_channel_scale":
            key = f"CH{arguments['channel']}"
            before = self.state["status"]["channels"][key]["scale_v_per_div"]
            self.state["status"]["channels"][key]["scale_v_per_div"] = arguments["volts_per_div"]
            return {"channel": arguments["channel"], "before": before, "after": arguments["volts_per_div"], "unit": "V/div"}
        if tool == "set_timebase_scale":
            before = self.state["status"]["timebase"]["scale_s_per_div"]
            self.state["status"]["timebase"]["scale_s_per_div"] = arguments["seconds_per_div"]
            return {"before": before, "after": arguments["seconds_per_div"], "unit": "s/div"}
        if tool == "set_trigger_level":
            before = self.state["status"]["trigger"]["level_v"]
            self.state["status"]["trigger"]["level_v"] = arguments["level_v"]
            return {"before": before, "after": arguments["level_v"], "unit": "V"}
        if tool in {"run", "stop", "single"}:
            before = self.state["status"]["trigger"]["status"]
            after = {"run": "AUTO", "stop": "STOP", "single": "WAIT"}[tool]
            self.state["status"]["trigger"]["status"] = after
            return {"before": before, "after": after, "action": tool}
        raise KeyError(f"未知模拟工具: {tool}")

    def _samples(self) -> tuple[list[float], list[float], float]:
        points = 1200
        scale = float(self.state["status"]["channels"]["CH1"]["scale_v_per_div"])
        offset = float(self.state["status"]["channels"]["CH1"].get("offset_v", 0.0))
        timebase = float(self.state["status"]["timebase"]["scale_s_per_div"])
        window = max(timebase * 12.0, 1e-9)
        times = [index * window / (points - 1) for index in range(points)]
        quantization = max(scale / 25.0, 1e-9)
        capture_index = self.capture_count - 1 if self.capture_count > 0 else self.capture_count
        phase = capture_index * 0.173

        if self.scenario == "sine":
            analog = [0.2 + math.sin(2.0 * math.pi * 1000.0 * value + phase) for value in times]
        elif self.scenario == "low_frequency":
            analog = [0.1 + 0.8 * math.sin(2.0 * math.pi * 2.0 * value + phase) for value in times]
        elif self.scenario == "high_frequency":
            analog = [0.1 + 0.8 * math.sin(2.0 * math.pi * 10_000_000.0 * value + phase) for value in times]
        elif self.scenario == "dc":
            analog = [1.5 + 0.002 * math.sin(2.0 * math.pi * 17.0 * value + phase) for value in times]
        elif self.scenario == "noise":
            analog = [
                0.18 * math.sin(2.0 * math.pi * 1731.0 * value + phase)
                + 0.11 * math.sin(2.0 * math.pi * 3917.0 * value + 0.4)
                + 0.06 * math.sin(2.0 * math.pi * 7919.0 * value + 1.1)
                for value in times
            ]
        elif self.scenario == "step":
            analog = [0.1 if index < points // 2 else 1.1 for index in range(points)]
        elif self.scenario == "clipped":
            analog = [20.0 * math.sin(2.0 * math.pi * 1000.0 * value + phase) for value in times]
        else:
            drift = 0.0002 if self.capture_count % 2 == 0 else -0.0002
            analog = [drift + 0.00002 * math.sin(2.0 * math.pi * 137.0 * value + phase) for value in times]

        lower = offset - scale * 4.0
        upper = offset + scale * 4.0
        samples = [
            min(upper, max(lower, round(value / quantization) * quantization))
            for value in analog
        ]
        return times, samples, quantization

    def close(self) -> None:
        return None
