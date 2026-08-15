from __future__ import annotations

import math
import unittest

from src.utils import parse_float, redact_serial


class UtilityTests(unittest.TestCase):
    def test_invalid_measurement_sentinel_becomes_none(self) -> None:
        self.assertIsNone(parse_float("9.9E37"))
        self.assertIsNone(parse_float("not-a-number"))
        self.assertIsNone(parse_float(str(math.inf)))

    def test_regular_measurement_is_preserved(self) -> None:
        self.assertEqual(parse_float("1.25e3"), 1250.0)

    def test_idn_serial_is_redacted(self) -> None:
        value = "RIGOL TECHNOLOGIES,DS1102Z-E,TESTSERIAL12345,00.06.01"
        redacted = redact_serial(value)
        self.assertIn("TES***345", redacted)
        self.assertNotIn("TESTSERIAL12345", redacted)


if __name__ == "__main__":
    unittest.main()
