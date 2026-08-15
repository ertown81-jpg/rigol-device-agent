from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rigol_agent.adapter import SimulatedRigolAdapter
from rigol_agent.agent import RigolAgent
from rigol_agent.planner import RuleBasedPlanner
from rigol_agent.policy import ExecutionPolicy
from rigol_agent.tools import TOOL_SPECS, ToolRegistry


class AgentExecutionTests(unittest.TestCase):
    def test_read_only_task_executes_and_is_audited(self) -> None:
        adapter = SimulatedRigolAdapter()
        registry = ToolRegistry(adapter, ExecutionPolicy())
        with tempfile.TemporaryDirectory() as directory:
            agent = RigolAgent(RuleBasedPlanner(), registry, Path(directory) / "sessions")
            result = agent.run("读取 CH1 的频率和峰峰值")
            self.assertTrue(result.success)
            self.assertEqual(result.results[0].tool, "measure")
            self.assertTrue(Path(result.output_path or "").exists())
            self.assertTrue(Path(result.report_path or "").exists())
            self.assertTrue((Path(directory) / "audit.jsonl").exists())

    def test_change_is_blocked_without_explicit_permission(self) -> None:
        adapter = SimulatedRigolAdapter()
        registry = ToolRegistry(adapter, ExecutionPolicy())
        with tempfile.TemporaryDirectory() as directory:
            result = RigolAgent(
                RuleBasedPlanner(), registry, Path(directory) / "sessions"
            ).run("开启 CH2")
        self.assertFalse(result.success)
        self.assertIn("--allow-changes", result.results[0].error or "")
        self.assertFalse(adapter.state["status"]["channels"]["CH2"]["enabled"])

    def test_change_executes_with_permission(self) -> None:
        adapter = SimulatedRigolAdapter()
        registry = ToolRegistry(adapter, ExecutionPolicy(allow_changes=True))
        with tempfile.TemporaryDirectory() as directory:
            result = RigolAgent(
                RuleBasedPlanner(), registry, Path(directory) / "sessions"
            ).run("开启 CH2")
        self.assertTrue(result.success)
        self.assertTrue(adapter.state["status"]["channels"]["CH2"]["enabled"])

    def test_every_model_tool_uses_strict_closed_schema(self) -> None:
        for spec in TOOL_SPECS:
            schema = spec.openai_schema()
            self.assertTrue(schema["strict"])
            self.assertFalse(schema["parameters"]["additionalProperties"])
            self.assertEqual(
                set(schema["parameters"]["properties"]),
                set(schema["parameters"]["required"]),
            )

    def test_raw_waveform_requires_guarded_permission(self) -> None:
        adapter = SimulatedRigolAdapter()
        registry = ToolRegistry(adapter, ExecutionPolicy())
        with self.assertRaisesRegex(Exception, "--allow-guarded"):
            registry.execute(
                "capture_waveform",
                {"channel": 1, "mode": "RAW", "max_points": 1000},
            )


if __name__ == "__main__":
    unittest.main()
