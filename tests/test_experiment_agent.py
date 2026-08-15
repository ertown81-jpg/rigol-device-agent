from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from rigol_agent.adapter import SimulatedRigolAdapter
from rigol_agent.adaptive import ClosedLoopSignalAgent
from rigol_agent.agent import RigolAgent
from rigol_agent.memory import ExperimentMemory
from rigol_agent.planner import RuleBasedPlanner
from rigol_agent.policy import ExecutionPolicy, PolicyViolation
from rigol_agent.tools import ToolRegistry


class _FaultAdapter(SimulatedRigolAdapter):
    def __init__(self, output_dir: Path, *, fail_mutation: bool = False, fail_restore_readback: bool = False) -> None:
        super().__init__("sine", output_dir)
        self.fail_mutation = fail_mutation
        self.fail_restore_readback = fail_restore_readback
        self.mutated = False

    def invoke(self, tool, arguments):
        if tool == "set_timebase_scale" and self.fail_mutation and not self.mutated:
            self.state["status"]["timebase"]["scale_s_per_div"] = arguments["seconds_per_div"]
            self.mutated = True
            raise RuntimeError("写入后读回超时")
        if tool == "get_device_status" and self.fail_restore_readback and self.mutated:
            raise RuntimeError("恢复后无法读回")
        return super().invoke(tool, arguments)


class _RestoreVerificationFailureAdapter(SimulatedRigolAdapter):
    def __init__(self, output_dir: Path) -> None:
        super().__init__("sine", output_dir)
        self.mutation_seen = False
        self.fail_next_status = False
        self.restore_writes = 0

    def invoke(self, tool, arguments):
        if tool in {"set_channel_scale", "set_timebase_scale", "set_trigger_level"}:
            current_scale = self.state["status"]["channels"]["CH1"]["scale_v_per_div"]
            current_timebase = self.state["status"]["timebase"]["scale_s_per_div"]
            result = super().invoke(tool, arguments)
            changed = (
                tool == "set_channel_scale" and arguments["volts_per_div"] != current_scale
            ) or (
                tool == "set_timebase_scale" and arguments["seconds_per_div"] != current_timebase
            )
            if changed:
                self.mutation_seen = True
            elif self.mutation_seen:
                self.restore_writes += 1
                if self.restore_writes >= 1:
                    self.fail_next_status = True
            return result
        if tool == "get_device_status" and self.fail_next_status:
            self.fail_next_status = False
            raise RuntimeError("恢复后的状态读回失败")
        return super().invoke(tool, arguments)


class _ScreenshotFailureAdapter(SimulatedRigolAdapter):
    def invoke(self, tool, arguments):
        if tool == "capture_screen":
            raise TimeoutError("模拟截图超时")
        return super().invoke(tool, arguments)


def _run(scenario: str, directory: Path, *, lease: bool = True):
    adapter = SimulatedRigolAdapter(scenario, directory / "samples")
    tools = ToolRegistry(adapter, ExecutionPolicy())
    planner = RuleBasedPlanner()
    agent = RigolAgent(planner, tools, output_dir=directory / "sessions")
    agent.adaptive_runner = ClosedLoopSignalAgent(
        tools,
        planner,
        output_dir=directory / "sessions",
        allow_adaptive_changes=lease,
    )
    return adapter, agent.run("analyze CH1 signal")


class ExperimentAgentTests(unittest.TestCase):
    def test_known_signal_scenarios(self) -> None:
        expectations = {
            "sine": (True, "periodic"),
            "low_frequency": (True, "periodic"),
            "high_frequency": (True, "periodic"),
            "dc": (True, "dc_or_slow"),
            "noise": (True, "aperiodic_noise"),
            "step": (True, "transient_or_step"),
            "clipped": (True, "periodic"),
            "low_resolution": (False, None),
        }
        with tempfile.TemporaryDirectory() as root:
            for scenario, (scientific_success, hypothesis) in expectations.items():
                with self.subTest(scenario=scenario):
                    adapter, result = _run(scenario, Path(root) / scenario)
                    self.assertEqual(result.analysis["scientific_success"], scientific_success)
                    if hypothesis:
                        self.assertEqual(result.analysis["final_hypothesis"]["id"], hypothesis)
                    self.assertTrue(result.analysis["execution_success"])
                    self.assertEqual(result.analysis["restoration_status"], "restored")
                    self.assertEqual(adapter.state["status"]["channels"]["CH1"]["scale_v_per_div"], 2.0)
                    self.assertEqual(adapter.state["status"]["timebase"]["scale_s_per_div"], 0.001)
                    self.assertLessEqual(len(result.analysis["adaptive_iterations"]), 4)

    def test_clipped_signal_is_recovered_before_scientific_success(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _, result = _run("clipped", Path(root))
        iterations = result.analysis["adaptive_iterations"]
        self.assertFalse(iterations[0]["observation"]["validity"]["not_clipped"])
        self.assertEqual(iterations[0]["decision"]["experiment"]["id"], "clipping_recovery_test")
        self.assertTrue(iterations[-1]["observation"]["validity"]["not_clipped"])
        self.assertTrue(result.analysis["scientific_success"])

    def test_each_round_changes_at_most_one_variable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _, result = _run("noise", Path(root))
        for iteration in result.analysis["adaptive_iterations"]:
            self.assertLessEqual(len(iteration["executed_actions"]), 1)
            self.assertLessEqual(len(iteration["decision"]["actions"]), 1)

    def test_without_explicit_lease_no_device_mutation_occurs(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            adapter, result = _run("sine", Path(root), lease=False)
        self.assertFalse(result.analysis["adaptive_change_lease"]["granted"])
        self.assertEqual(result.analysis["stopping_reason"], "policy_blocked")
        self.assertEqual(result.analysis["adaptive_change_lease"]["used_mutations"], 0)
        self.assertEqual(adapter.state["status"]["timebase"]["scale_s_per_div"], 0.001)

    def test_unknown_quantization_never_gets_resolution_credit(self) -> None:
        from rigol_agent.adaptive import assess_quality

        quality = assess_quality([{
            "hypotheses": [{"id": "periodic"}],
            "observation": {
                "waveform": {"quantization_v": 0.0, "robust_span_v": 2.0, "vertical_span_divisions": 2.0, "median_v": 0.0},
                "measurements": {"frequency_hz": 1000.0},
                "validity": {"not_clipped": True, "period_coverage_ok": True},
                "consistency_ratio": 0.0,
                "signal_class": "周期信号",
                "stability": None,
            },
        }])
        self.assertLess(quality["score"], 0.68)
        self.assertTrue(any("量化步进未知" in reason for reason in quality["reasons"]))

    def test_non_finite_device_parameters_are_rejected(self) -> None:
        tools = ToolRegistry(SimulatedRigolAdapter(), ExecutionPolicy(allow_changes=True))
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises((ValueError, PolicyViolation)):
                tools.execute("set_trigger_level", {"level_v": value})

    def test_mutation_failure_stops_and_restores_full_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            adapter = _FaultAdapter(Path(root) / "samples", fail_mutation=True)
            tools = ToolRegistry(adapter, ExecutionPolicy())
            planner = RuleBasedPlanner()
            runner = ClosedLoopSignalAgent(tools, planner, output_dir=Path(root) / "sessions", allow_adaptive_changes=True)
            result = runner.run("analyze CH1 signal")
        self.assertEqual(result.analysis["stopping_reason"], "action_failed")
        self.assertFalse(result.analysis["execution_success"])
        self.assertEqual(adapter.state["status"]["timebase"]["scale_s_per_div"], 0.001)
        self.assertEqual(result.analysis["restoration_status"], "restored")

    def test_memory_is_factual_bounded_and_recalled(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            memory = ExperimentMemory(root_path / "memory.jsonl")
            adapter = SimulatedRigolAdapter("dc", root_path / "samples")
            tools = ToolRegistry(adapter, ExecutionPolicy())
            planner = RuleBasedPlanner()
            first = ClosedLoopSignalAgent(tools, planner, output_dir=root_path / "sessions", memory=memory, allow_adaptive_changes=True).run("analyze CH1 signal")
            second = ClosedLoopSignalAgent(tools, planner, output_dir=root_path / "sessions", memory=memory, allow_adaptive_changes=True).run("analyze CH1 signal")
            self.assertIsNotNone(first.analysis["memory_record"])
            self.assertEqual(len(second.analysis["prior_memory"]), 1)
            stored = (root_path / "memory.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("response_id", stored)
            self.assertNotIn("raw_actions", stored)

    def test_restore_verification_failure_is_never_reported_as_restored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            adapter = _RestoreVerificationFailureAdapter(Path(root) / "samples")
            tools = ToolRegistry(adapter, ExecutionPolicy())
            planner = RuleBasedPlanner()
            result = ClosedLoopSignalAgent(
                tools,
                planner,
                output_dir=Path(root) / "sessions",
                allow_adaptive_changes=True,
            ).run("analyze CH1 signal")
        self.assertFalse(result.analysis["settings_restored"])
        self.assertEqual(result.analysis["restoration_status"], "failed")
        self.assertEqual(result.analysis["stopping_reason"], "restore_failed")

    def test_screenshot_timeout_is_optional_when_structured_evidence_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            adapter = _ScreenshotFailureAdapter("sine", Path(root) / "samples")
            tools = ToolRegistry(adapter, ExecutionPolicy())
            planner = RuleBasedPlanner()
            result = ClosedLoopSignalAgent(
                tools,
                planner,
                output_dir=Path(root) / "sessions",
                allow_adaptive_changes=True,
            ).run("analyze CH1 signal")
        self.assertTrue(result.analysis["execution_success"])
        self.assertTrue(result.analysis["scientific_success"])
        self.assertTrue(any("截图失败" in warning for warning in result.analysis["warnings"]))


if __name__ == "__main__":
    unittest.main()
