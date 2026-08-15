from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.waveform import (
    capture_waveform,
    convert_byte_waveform,
    parse_preamble,
    save_csv,
)


class FakeScope:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def query(self, command: str) -> str:
        self.commands.append(command)
        responses = {
            ":TRIG:STAT?": "RUN",
            ":WAV:SOUR?": "CHAN2",
            ":WAV:MODE?": "NORM",
            ":WAV:FORM?": "WORD",
            ":WAV:STAR?": "2",
            ":WAV:STOP?": "4",
            ":WAV:PRE?": "0,0,5,1,0.1,-0.2,0,0.02,100,10",
        }
        if command in responses:
            return responses[command]
        raise AssertionError(f"unexpected query: {command}")

    def write(self, command: str) -> None:
        self.commands.append(command)

    def stop(self) -> None:
        self.commands.append(":STOP")

    def run(self) -> None:
        self.commands.append(":RUN")

    def query_binary_values(self, command: str, **kwargs: object) -> list[int]:
        self.commands.append(command)
        return [110, 111, 112, 113, 114]


class WaveformTests(unittest.TestCase):
    def test_parse_and_convert_preamble(self) -> None:
        preamble = parse_preamble("0,0,3,1,0.5,-1,1,0.1,100,10")
        times, volts = convert_byte_waveform([110, 111, 112], preamble)
        self.assertEqual(times, [-1.5, -1.0, -0.5])
        self.assertEqual(volts, [0.0, 0.1, 0.2])

    def test_bad_preamble_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_preamble("0,1,2")

    def test_raw_capture_stops_batches_and_restores_run(self) -> None:
        scope = FakeScope()
        capture = capture_waveform(scope, channel=1, mode="RAW")
        self.assertEqual(capture.points, 5)
        self.assertIn(":STOP", scope.commands)
        self.assertIn(":WAV:MODE RAW", scope.commands)
        self.assertIn(":WAV:STAR 1", scope.commands)
        self.assertIn(":WAV:STOP 5", scope.commands)
        self.assertIn(":WAV:SOUR CHAN2", scope.commands)
        self.assertIn(":WAV:MODE NORM", scope.commands)
        self.assertIn(":WAV:FORM WORD", scope.commands)
        self.assertIn(":WAV:STAR 2", scope.commands)
        self.assertIn(":WAV:STOP 4", scope.commands)
        self.assertEqual(scope.commands[-1], ":RUN")

    def test_save_csv(self) -> None:
        scope = FakeScope()
        capture = capture_waveform(scope, channel=1, mode="NORMAL")
        with tempfile.TemporaryDirectory() as directory:
            path = save_csv(capture, Path(directory) / "wave.csv")
            text = path.read_text(encoding="utf-8-sig")
        self.assertIn("time_s,voltage_v,channel", text)
        self.assertIn("CH1", text)


if __name__ == "__main__":
    unittest.main()
