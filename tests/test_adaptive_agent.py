from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rigol_agent.adapter import SimulatedRigolAdapter
from rigol_agent.adaptive import ClosedLoopSignalAgent, assess_quality
from rigol_agent.agent import RigolAgent
from rigol_agent.planner import RuleBasedPlanner
from rigol_agent.policy import ExecutionPolicy
from rigol_agent.tools import ToolRegistry


class AdaptiveAgentTests(unittest.TestCase):
    def test_closed_loop_adjusts_remeasures_and_restores(self) -> None:
        adapter = SimulatedRigolAdapter()
        tools = ToolRegistry(adapter, ExecutionPolicy())
        planner = RuleBasedPlanner()
        with tempfile.TemporaryDirectory() as directory:
            agent = RigolAgent(planner, tools, output_dir=Path(directory))
            agent.adaptive_runner = ClosedLoopSignalAgent(
                tools,
                planner,
                output_dir=Path(directory),
                allow_adaptive_changes=True,
            )
            result = agent.run("检查 CH1 当前信号，把它测清楚")

        self.assertTrue(result.success)
        self.assertTrue(result.analysis["execution_success"])
        self.assertTrue(result.analysis["scientific_success"])
        self.assertTrue(result.analysis["settings_restored"])
        self.assertGreaterEqual(len(result.analysis["adaptive_iterations"]), 2)
        tools_used = [step.tool for step in result.plan.steps]
        self.assertIn("set_timebase_scale", tools_used)
        self.assertEqual(adapter.state["status"]["channels"]["CH1"]["scale_v_per_div"], 2.0)
        self.assertEqual(adapter.state["status"]["timebase"]["scale_s_per_div"], 0.001)

    def test_explicit_measurement_request_stays_single_pass(self) -> None:
        self.assertFalse(ClosedLoopSignalAgent.accepts("读取 CH1 的频率和峰峰值"))
        self.assertTrue(ClosedLoopSignalAgent.accepts("分析 CH1 信号并自动调到看清楚"))

    def test_one_quantization_step_is_not_high_confidence(self) -> None:
        iterations = [
            {
                "observation": {
                    "waveform": {
                        "quantization_v": 0.004,
                        "robust_span_v": 0.004,
                        "vertical_span_divisions": 0.04,
                    },
                    "consistency_ratio": 0.0,
                    "signal_class": "低于有效分辨率的微小活动",
                    "stability": {
                        "class_unchanged": False,
                        "median_delta_v": 0.062,
                    },
                }
            }
        ]
        quality = assess_quality(iterations)
        self.assertLess(quality["score"], 0.68)
        self.assertEqual(quality["level"], "低")


if __name__ == "__main__":
    unittest.main()
