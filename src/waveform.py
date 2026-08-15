from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .utils import ensure_parent


@dataclass(frozen=True)
class WaveformPreamble:
    format: int
    type: int
    points: int
    count: int
    x_increment: float
    x_origin: float
    x_reference: float
    y_increment: float
    y_origin: float
    y_reference: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WaveformCapture:
    channel: int
    mode: str
    preamble: WaveformPreamble
    raw_values: list[int]
    time_s: list[float]
    voltage_v: list[float]

    @property
    def points(self) -> int:
        return len(self.raw_values)

    def summary(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "mode": self.mode,
            "points": self.points,
            "minimum_v": min(self.voltage_v) if self.voltage_v else None,
            "maximum_v": max(self.voltage_v) if self.voltage_v else None,
            "preamble": self.preamble.to_dict(),
        }


def parse_preamble(text: str) -> WaveformPreamble:
    fields = [part.strip() for part in text.strip().split(",")]
    if len(fields) != 10:
        raise ValueError(f"波形前导应包含 10 个字段，实际为 {len(fields)}: {text!r}")
    return WaveformPreamble(
        format=int(fields[0]),
        type=int(fields[1]),
        points=int(fields[2]),
        count=int(fields[3]),
        x_increment=float(fields[4]),
        x_origin=float(fields[5]),
        x_reference=float(fields[6]),
        y_increment=float(fields[7]),
        y_origin=float(fields[8]),
        y_reference=float(fields[9]),
    )


def convert_byte_waveform(
    raw_values: Iterable[int],
    preamble: WaveformPreamble,
    start_index: int = 0,
) -> tuple[list[float], list[float]]:
    values = [int(value) for value in raw_values]
    times = [
        ((start_index + index) - preamble.x_reference) * preamble.x_increment
        + preamble.x_origin
        for index in range(len(values))
    ]
    voltages = [
        (value - preamble.y_origin - preamble.y_reference) * preamble.y_increment
        for value in values
    ]
    return times, voltages


def capture_waveform(
    scope: Any,
    channel: int,
    mode: str = "NORMAL",
    max_points: int | None = None,
) -> WaveformCapture:
    if channel not in (1, 2):
        raise ValueError("DS1102Z-E 仅支持通道 1 或 2")
    normalized_mode = mode.strip().upper()
    if normalized_mode not in {"NORMAL", "RAW"}:
        raise ValueError("波形模式必须是 NORMAL 或 RAW")

    original_trigger_status = scope.query(":TRIG:STAT?").strip().upper()
    original_waveform_state = {
        "source": scope.query(":WAV:SOUR?").strip(),
        "mode": scope.query(":WAV:MODE?").strip(),
        "format": scope.query(":WAV:FORM?").strip(),
        "start": scope.query(":WAV:STAR?").strip(),
        "stop": scope.query(":WAV:STOP?").strip(),
    }
    restore_run = normalized_mode == "RAW" and original_trigger_status != "STOP"
    if normalized_mode == "RAW":
        scope.stop()

    try:
        scope.write(f":WAV:SOUR CHAN{channel}")
        scope.write(f":WAV:MODE {'NORM' if normalized_mode == 'NORMAL' else 'RAW'}")
        scope.write(":WAV:FORM BYTE")
        preamble = parse_preamble(scope.query(":WAV:PRE?"))
        total_points = preamble.points
        if max_points is not None:
            if max_points <= 0:
                raise ValueError("max_points 必须大于 0")
            total_points = min(total_points, max_points)
        if total_points <= 0:
            raise RuntimeError("示波器返回的波形点数为 0")

        raw_values: list[int] = []
        batch_limit = 250_000
        for start in range(1, total_points + 1, batch_limit):
            stop = min(start + batch_limit - 1, total_points)
            scope.write(f":WAV:STAR {start}")
            scope.write(f":WAV:STOP {stop}")
            batch = scope.query_binary_values(
                ":WAV:DATA?",
                datatype="B",
                container=list,
                expect_termination=False,
            )
            expected = stop - start + 1
            if len(batch) != expected:
                raise RuntimeError(
                    f"波形分块长度不完整：请求 {expected} 点，收到 {len(batch)} 点"
                )
            raw_values.extend(int(value) for value in batch)

        if not raw_values:
            raise RuntimeError("未收到波形数据")
        if len(raw_values) > total_points:
            raw_values = raw_values[:total_points]
        times, voltages = convert_byte_waveform(raw_values, preamble)
        return WaveformCapture(
            channel=channel,
            mode=normalized_mode,
            preamble=preamble,
            raw_values=raw_values,
            time_s=times,
            voltage_v=voltages,
        )
    finally:
        try:
            scope.write(f":WAV:SOUR {original_waveform_state['source']}")
            scope.write(f":WAV:MODE {original_waveform_state['mode']}")
            scope.write(f":WAV:FORM {original_waveform_state['format']}")
            scope.write(f":WAV:STAR {original_waveform_state['start']}")
            scope.write(f":WAV:STOP {original_waveform_state['stop']}")
        finally:
            if restore_run:
                scope.run()


def save_csv(capture: WaveformCapture, path: str | Path) -> Path:
    output = ensure_parent(path)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", "voltage_v", "channel"])
        writer.writerows(
            zip(
                capture.time_s,
                capture.voltage_v,
                [f"CH{capture.channel}"] * capture.points,
            )
        )
    return output
