from __future__ import annotations

import unittest

from rigol_agent.planner import RuleBasedPlanner


class RuleBasedPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = RuleBasedPlanner()

    def test_combined_signal_request_becomes_safe_multistep_plan(self) -> None:
        plan = self.planner.plan(
            "检查 CH1 当前信号，读取频率、峰峰值和有效值，保存波形和截图"
        )
        self.assertEqual(
            [step.tool for step in plan.steps],
            ["get_device_status", "measure", "capture_waveform", "capture_screen"],
        )
        self.assertEqual(
            plan.steps[1].arguments["measurements"],
            ["FREQUENCY", "VPP", "RMS"],
        )

    def test_write_request_parses_units(self) -> None:
        plan = self.planner.plan(
            "开启 CH2，把 CH2 垂直档位设为 500 mV，时基设为 200 us，触发电平设为 -100 mV"
        )
        by_name = {step.tool: step.arguments for step in plan.steps}
        self.assertEqual(by_name["set_channel_enabled"], {"channel": 2, "enabled": True})
        self.assertAlmostEqual(by_name["set_channel_scale"]["volts_per_div"], 0.5)
        self.assertAlmostEqual(by_name["set_timebase_scale"]["seconds_per_div"], 200e-6)
        self.assertAlmostEqual(by_name["set_trigger_level"]["level_v"], -0.1)

    def test_unknown_request_is_not_guessed(self) -> None:
        plan = self.planner.plan("帮我把实验做得漂亮一点")
        self.assertEqual(plan.steps, [])


if __name__ == "__main__":
    unittest.main()
