from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from rigol_agent.adapter import SimulatedRigolAdapter
from rigol_agent.model_planner import CompatibleModelPlanner, FallbackPlanner
from rigol_agent.policy import ExecutionPolicy
from rigol_agent.tools import ToolRegistry


class _FakeCompletions:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def create(self, **kwargs):
        call = SimpleNamespace(
            function=SimpleNamespace(arguments=json.dumps(self.payload, ensure_ascii=False))
        )
        message = SimpleNamespace(tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self, payload: dict) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))


class ModelPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = ToolRegistry(SimulatedRigolAdapter(), ExecutionPolicy())

    def test_deepseek_compatible_plan_is_locally_validated(self) -> None:
        client = _FakeClient(
            {
                "steps": [
                    {"tool": "get_device_status", "arguments": {}, "reason": "确认在线"},
                    {
                        "tool": "measure",
                        "arguments": {"channel": 1, "measurements": ["FREQUENCY", "VPP"]},
                        "reason": "读取测量值",
                    },
                ]
            }
        )
        planner = CompatibleModelPlanner(
            self.tools,
            provider="deepseek",
            model="test-model",
            client=client,
        )
        plan = planner.plan("读取 CH1 的频率和峰峰值")
        self.assertEqual([step.tool for step in plan.steps], ["get_device_status", "measure"])
        self.assertEqual(plan.planner, "deepseek:test-model")

    def test_invalid_model_arguments_fall_back_to_rules(self) -> None:
        client = _FakeClient(
            {
                "steps": [
                    {
                        "tool": "measure",
                        "arguments": {"channel": 3, "measurements": ["FREQUENCY"]},
                        "reason": "invalid",
                    }
                ]
            }
        )
        primary = CompatibleModelPlanner(
            self.tools,
            provider="deepseek",
            model="test-model",
            client=client,
        )
        planner = FallbackPlanner(primary)
        plan = planner.plan("读取 CH1 的频率")
        self.assertEqual(plan.steps[0].tool, "measure")
        self.assertEqual(plan.steps[0].arguments["channel"], 1)
        self.assertTrue(plan.planner.endswith("->rules"))
        self.assertIn("ValueError", planner.last_error)

    def test_registry_rejects_unknown_or_out_of_range_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "允许值"):
            self.tools.validate("measure", {"channel": 3, "measurements": ["FREQUENCY"]})
        with self.assertRaisesRegex(ValueError, "未知参数"):
            self.tools.validate("capture_screen", {"raw_scpi": "*RST"})


if __name__ == "__main__":
    unittest.main()
