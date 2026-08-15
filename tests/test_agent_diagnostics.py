from __future__ import annotations

import unittest

from rigol_agent.diagnostics import analyze_results
from rigol_agent.models import ToolResult


class DiagnosticTests(unittest.TestCase):
    def test_invalid_frequency_is_explained_not_treated_as_zero(self) -> None:
        result = ToolResult(
            tool="measure", arguments={}, success=True,
            started_at="a", finished_at="b",
            data={"measurements": {"FREQUENCY": None, "VPP": 0.8}},
        )
        analysis = analyze_results([result])
        self.assertIn("不是数值为 0", analysis["conclusion"])
        self.assertIn("峰峰值：0.8 V", analysis["conclusion"])


if __name__ == "__main__":
    unittest.main()
