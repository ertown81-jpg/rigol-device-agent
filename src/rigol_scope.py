from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from .connection import VisaConnection
from .utils import ensure_parent, parse_float, redact_serial
from .waveform import WaveformCapture, capture_waveform


MEASUREMENT_ITEMS = {
    "FREQUENCY": "FREQ",
    "FREQ": "FREQ",
    "PERIOD": "PER",
    "MAXIMUM": "VMAX",
    "VMAX": "VMAX",
    "MINIMUM": "VMIN",
    "VMIN": "VMIN",
    "PEAK_TO_PEAK": "VPP",
    "VPP": "VPP",
    "AVERAGE": "VAVG",
    "VAVG": "VAVG",
    "RMS": "VRMS",
    "VRMS": "VRMS",
    "POSITIVE_DUTY": "PDUT",
    "PDUTY": "PDUT",
    "NEGATIVE_DUTY": "NDUT",
    "NDUTY": "NDUT",
    "RISE_TIME": "RTIM",
    "RTIME": "RTIM",
    "FALL_TIME": "FTIM",
    "FTIME": "FTIM",
}


class RigolDS1102ZE:
    def __init__(
        self,
        resource: str,
        *,
        backend: str | None = None,
        timeout_ms: int = 10_000,
        query_delay_s: float = 0.05,
        logger: logging.Logger | None = None,
    ) -> None:
        self.connection = VisaConnection(
            resource_name=resource,
            backend=backend,
            timeout_ms=timeout_ms,
            query_delay_s=query_delay_s,
        )
        self.logger = logger or logging.getLogger("rigol_ds1102ze")

    @property
    def resource(self) -> str:
        return self.connection.resource_name

    @property
    def instrument(self) -> Any:
        if self.connection.instrument is None:
            raise RuntimeError("尚未连接示波器")
        return self.connection.instrument

    def connect(self) -> None:
        started = time.perf_counter()
        self.connection.open()
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.logger.info("CONNECT resource=%s elapsed_ms=%.1f success=true", self.resource, elapsed_ms)

    def close(self) -> None:
        self.connection.close()
        self.logger.info("CLOSE resource=%s", self.resource)

    def __enter__(self) -> "RigolDS1102ZE":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def write(self, command: str) -> None:
        started = time.perf_counter()
        try:
            self.instrument.write(command)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.error(
                "WRITE command=%r elapsed_ms=%.1f success=false error=%r",
                command,
                elapsed_ms,
                exc,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.logger.info(
            "WRITE command=%r elapsed_ms=%.1f success=true",
            command,
            elapsed_ms,
        )

    def query(self, command: str) -> str:
        started = time.perf_counter()
        try:
            response = self.instrument.query(command).strip()
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.error(
                "QUERY command=%r elapsed_ms=%.1f success=false error=%r",
                command,
                elapsed_ms,
                exc,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.logger.info(
            "QUERY command=%r response=%r elapsed_ms=%.1f success=true",
            command,
            redact_serial(response),
            elapsed_ms,
        )
        return response

    def query_binary_values(self, command: str, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            values = self.instrument.query_binary_values(command, **kwargs)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.logger.error(
                "BINARY_QUERY command=%r elapsed_ms=%.1f success=false error=%r",
                command,
                elapsed_ms,
                exc,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.logger.info(
            "BINARY_QUERY command=%r values=%d elapsed_ms=%.1f success=true",
            command,
            len(values),
            elapsed_ms,
        )
        return values

    def identify(self) -> dict[str, str]:
        response = self.query("*IDN?")
        fields = [field.strip() for field in response.split(",")]
        fields += [""] * (4 - len(fields))
        return {
            "manufacturer": fields[0],
            "model": fields[1],
            "serial": fields[2],
            "firmware": fields[3],
            "raw": response,
        }

    def get_errors(self, limit: int = 20) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        for _ in range(limit):
            response = self.query(":SYST:ERR?")
            code_text, _, message = response.partition(",")
            try:
                code = int(code_text.strip())
            except ValueError:
                errors.append({"code": None, "message": response})
                break
            if code == 0:
                break
            errors.append({"code": code, "message": message.strip().strip('"')})
        return errors

    def get_channel_config(self, channel: int) -> dict[str, Any]:
        self._validate_channel(channel)
        prefix = f":CHAN{channel}"
        return {
            "enabled": self.query(f"{prefix}:DISP?") == "1",
            "scale_v_per_div": parse_float(self.query(f"{prefix}:SCAL?")),
            "offset_v": parse_float(self.query(f"{prefix}:OFFS?")),
            "coupling": self.query(f"{prefix}:COUP?"),
            "probe_ratio": parse_float(self.query(f"{prefix}:PROB?")),
            "bandwidth_limit": self.query(f"{prefix}:BWL?"),
        }

    def get_status(self) -> dict[str, Any]:
        trigger_mode = self.query(":TRIG:MODE?")
        trigger: dict[str, Any] = {
            "status": self.query(":TRIG:STAT?"),
            "mode": trigger_mode,
            "sweep": self.query(":TRIG:SWE?"),
            "coupling": self.query(":TRIG:COUP?"),
        }
        if trigger_mode.upper().startswith("EDGE"):
            trigger.update(
                {
                    "source": self.query(":TRIG:EDGE:SOUR?"),
                    "level_v": parse_float(self.query(":TRIG:EDGE:LEV?")),
                    "slope": self.query(":TRIG:EDGE:SLOP?"),
                }
            )
        return {
            "channels": {
                "CH1": self.get_channel_config(1),
                "CH2": self.get_channel_config(2),
            },
            "timebase": {
                "scale_s_per_div": parse_float(self.query(":TIM:SCAL?")),
                "offset_s": parse_float(self.query(":TIM:OFFS?")),
            },
            "acquisition": {
                "sample_rate_sa_s": parse_float(self.query(":ACQ:SRAT?")),
                "memory_depth_points": self.query(":ACQ:MDEP?"),
                "mode": self.query(":ACQ:TYPE?"),
                "averages": self.query(":ACQ:AVER?"),
            },
            "trigger": trigger,
        }

    def set_channel_enabled(self, channel: int, enabled: bool) -> None:
        self._validate_channel(channel)
        self.write(f":CHAN{channel}:DISP {1 if enabled else 0}")

    def set_channel_scale(self, channel: int, volts_per_div: float) -> None:
        self._validate_channel(channel)
        if volts_per_div <= 0:
            raise ValueError("垂直档位必须大于 0")
        self.write(f":CHAN{channel}:SCAL {volts_per_div:.12g}")

    def set_timebase_scale(self, seconds_per_div: float) -> None:
        if seconds_per_div <= 0:
            raise ValueError("时基必须大于 0")
        self.write(f":TIM:SCAL {seconds_per_div:.12g}")

    def set_trigger_level(self, level_v: float) -> None:
        self.write(f":TRIG:EDGE:LEV {level_v:.12g}")

    def run(self) -> None:
        self.write(":RUN")

    def stop(self) -> None:
        self.write(":STOP")

    def single(self) -> None:
        self.write(":SING")

    def force_trigger(self) -> None:
        self.write(":TFOR")

    def measure(self, channel: int, measurement: str) -> float | None:
        self._validate_channel(channel)
        normalized = measurement.strip().upper().replace("-", "_").replace(" ", "_")
        item = MEASUREMENT_ITEMS.get(normalized)
        if item is None:
            raise ValueError(
                f"不支持的测量项 {measurement!r}；可用项: {', '.join(sorted(MEASUREMENT_ITEMS))}"
            )
        return parse_float(self.query(f":MEAS:ITEM? {item},CHAN{channel}"))

    def capture_waveform(
        self,
        channel: int,
        *,
        mode: str = "NORMAL",
        max_points: int | None = None,
    ) -> WaveformCapture:
        return capture_waveform(self, channel, mode=mode, max_points=max_points)

    def capture_screen(self, output_path: str | Path) -> Path:
        output = ensure_parent(output_path)
        instrument = self.instrument
        original_read_termination = instrument.read_termination
        original_timeout = instrument.timeout
        started = time.perf_counter()
        try:
            # DS1000Z-E returns a definite-length TMC block. Reading the header
            # and payload explicitly avoids NI-VISA waiting for a text
            # terminator after the complete PNG has already arrived.
            instrument.read_termination = None
            instrument.timeout = max(original_timeout, 60_000)
            self.write(":DISP:DATA? ON,OFF,PNG")
            prefix = bytes(
                instrument.read_bytes(
                    2,
                    chunk_size=1_048_576,
                    break_on_termchar=False,
                )
            )
            if len(prefix) != 2 or prefix[:1] != b"#" or not prefix[1:2].isdigit():
                raise RuntimeError(f"截图响应的 TMC 头无效: {prefix!r}")
            length_digits = int(prefix[1:2])
            length_text = bytes(
                instrument.read_bytes(
                    length_digits,
                    chunk_size=1_048_576,
                    break_on_termchar=False,
                )
            )
            payload_length = int(length_text.decode("ascii"))
            image = bytes(
                instrument.read_bytes(
                    payload_length,
                    chunk_size=1_048_576,
                    break_on_termchar=False,
                )
            )
        finally:
            instrument.read_termination = original_read_termination
            instrument.timeout = original_timeout
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(f"截图响应不是有效 PNG 数据（收到 {len(image)} 字节）")
        output.write_bytes(image)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.logger.info(
            "SCREENSHOT path=%s bytes=%d elapsed_ms=%.1f",
            output,
            len(image),
            elapsed_ms,
        )
        return output

    @staticmethod
    def _validate_channel(channel: int) -> None:
        if channel not in (1, 2):
            raise ValueError("DS1102Z-E 仅支持通道 1 或 2")
