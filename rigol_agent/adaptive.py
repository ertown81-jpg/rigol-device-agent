from __future__ import annotations

import csv
import json
import math
import re
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.utils import now_iso, save_json

from .diagnostics import analyze_results
from .memory import ExperimentMemory
from .models import PlanStep, TaskPlan, TaskResult, ToolResult
from .policy import ExecutionPolicy
from .reporting import save_html_report
from .tools import ToolRegistry


SCALE_LADDER = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
TIMEBASE_LADDER = (
    5e-9, 10e-9, 20e-9, 50e-9,
    100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6,
    100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3,
    0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
)


@dataclass(frozen=True)
class AdaptiveCapabilityLease:
    channel: int
    max_mutations: int = 3
    allowed_tools: tuple[str, ...] = (
        "set_channel_scale",
        "set_timebase_scale",
        "set_trigger_level",
    )

    def authorize(self, tool: str, arguments: dict[str, Any], used: int) -> None:
        if tool not in self.allowed_tools:
            raise PermissionError(f"实验租约不允许工具 {tool}")
        if used >= self.max_mutations:
            raise PermissionError("实验租约的修改次数已用完")
        if tool == "set_channel_scale" and int(arguments.get("channel", 0)) != self.channel:
            raise PermissionError("实验租约不允许修改其他通道")


class ClosedLoopSignalAgent:
    """Bounded observe-decide-adjust loop for investigating a signal."""

    def __init__(
        self,
        read_tools: ToolRegistry,
        planner: Any,
        *,
        output_dir: str | Path = "output/agent/sessions",
        max_rounds: int = 4,
        memory: ExperimentMemory | None = None,
        allow_adaptive_changes: bool = False,
        allowed_change_tools: tuple[str, ...] = (
            "set_channel_scale",
            "set_timebase_scale",
            "set_trigger_level",
        ),
    ) -> None:
        self.read_tools = read_tools
        self.change_tools = ToolRegistry(
            read_tools.adapter,
            ExecutionPolicy(
                allow_changes=True,
                allow_guarded=False,
                device_label=read_tools.policy.device_label,
                argument_validator=read_tools.policy.argument_validator,
            ),
            read_tools.specs,
            read_tools.result_validator,
        )
        self.output_dir = Path(output_dir)
        self.max_rounds = max(2, min(int(max_rounds), 5))
        self.memory = memory or ExperimentMemory(self.output_dir.parent / "experiment_memory.jsonl")
        self.allow_adaptive_changes = bool(allow_adaptive_changes)
        self.allowed_change_tools = tuple(allowed_change_tools)
        self.decision_engine = AdaptiveDecisionEngine(planner, self.change_tools)

    @staticmethod
    def accepts(request: str) -> bool:
        text = request.lower()
        return bool(
            re.search(r"(?:检查|分析|诊断|测清楚|自动调|看清).{0,16}(?:ch[12]|通道\s*[12])?.{0,12}信号", text)
            or re.search(r"(?:investigate|analy[sz]e|inspect).{0,20}(?:ch[12]\s+)?signal", text)
        )

    def run(self, request: str) -> TaskResult:
        channel = _channel(request)
        lease = (
            AdaptiveCapabilityLease(channel=channel, allowed_tools=self.allowed_change_tools)
            if self.allow_adaptive_changes
            else None
        )
        started_at = now_iso()
        session_id = f"{started_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        plan = TaskPlan(
            request=request,
            steps=[],
            summary=f"闭环信号分析，最多 {self.max_rounds} 轮",
            planner=f"adaptive:{self.decision_engine.name}",
            planning={"mode": "closed_loop", "max_rounds": self.max_rounds},
        )
        results: list[ToolResult] = []
        iterations: list[dict[str, Any]] = []
        prior_memory = self.memory.recall(channel=channel, limit=3)
        original: dict[str, float] | None = None
        changed: set[str] = set()
        mutation_intent: set[str] = set()
        mutation_count = 0
        action_failed = False
        policy_blocked = False
        observation_failed = False
        restore_errors: list[str] = []

        try:
            for round_number in range(1, self.max_rounds + 1):
                status_result = self._execute(
                    session_id,
                    plan,
                    results,
                    "get_device_status",
                    {},
                    f"第 {round_number} 轮：读取当前设置与设备状态",
                    allow_changes=False,
                )
                if not status_result.success:
                    observation_failed = True
                    break
                status = status_result.data["status"]
                settings = _settings(status, channel)
                if original is None:
                    original = dict(settings)
                if not settings["channel_enabled"]:
                    iterations.append(
                        {
                            "round": round_number,
                            "settings": settings,
                            "observation": {},
                            "hypotheses": [],
                            "decision": {
                                "hypothesis_id": "no_valid_observation",
                                "hypothesis": "目标通道当前关闭",
                                "assessment": "闭环租约不允许自动开启通道，无法取得有效波形",
                                "confidence": 1.0,
                                "finish": True,
                                "actions": [],
                                "experiment": _experiment("channel_state_check", "检查目标通道是否可观测", ["通道关闭时不执行信号推断"], "用户明确开启通道后重试"),
                                "stopping_reason": "policy_blocked",
                                "source": "local_safety_controller",
                                "planning": {"duration_ms": 0.0},
                            },
                            "executed_actions": [],
                            "evidence_delta": {},
                            "screen_path": None,
                            "waveform_path": None,
                        }
                    )
                    policy_blocked = True
                    break

                measure_result = self._execute(
                    session_id,
                    plan,
                    results,
                    "measure",
                    {
                        "channel": channel,
                        "measurements": ["FREQUENCY", "VPP", "RMS", "VMAX", "VMIN"],
                    },
                    f"第 {round_number} 轮：读取频率、电压和幅度测量",
                    allow_changes=False,
                )
                waveform_result = self._execute(
                    session_id,
                    plan,
                    results,
                    "capture_waveform",
                    {"channel": channel, "mode": "NORMAL", "max_points": 10000},
                    f"第 {round_number} 轮：保存波形用于独立一致性检查",
                    allow_changes=False,
                )
                screen_result = self._execute(
                    session_id,
                    plan,
                    results,
                    "capture_screen",
                    {},
                    f"第 {round_number} 轮：保存屏幕证据",
                    allow_changes=False,
                )
                if not (measure_result.success and waveform_result.success):
                    observation_failed = True
                    break

                observation = build_observation(
                    settings,
                    measure_result.data,
                    waveform_result.data,
                    previous=iterations[-1]["observation"] if iterations else None,
                )
                hypotheses = generate_hypotheses(observation, iterations)
                decision = self.decision_engine.decide(
                    request=request,
                    round_number=round_number,
                    max_rounds=self.max_rounds,
                    settings=settings,
                    observation=observation,
                    hypotheses=hypotheses,
                    prior_memory=prior_memory,
                )
                iteration = {
                    "round": round_number,
                    "settings": settings,
                    "observation": observation,
                    "hypotheses": hypotheses,
                    "decision": decision,
                    "executed_actions": [],
                    "round_goal": decision.get("experiment", {}).get("question"),
                    "evidence_delta": build_evidence_delta(
                        observation,
                        iterations[-1]["observation"] if iterations else None,
                    ),
                    "screen_path": screen_result.data.get("output") if screen_result.success else None,
                    "screen_error": screen_result.error if not screen_result.success else None,
                    "waveform_path": waveform_result.data.get("csv_path"),
                }
                iterations.append(iteration)

                if round_number >= self.max_rounds or decision["finish"]:
                    break
                for action in decision["actions"]:
                    if lease is None:
                        decision["finish"] = True
                        decision["stopping_reason"] = "policy_blocked"
                        decision["assessment"] += "；当前任务没有自适应修改租约"
                        policy_blocked = True
                        break
                    try:
                        lease.authorize(action["tool"], action["arguments"], mutation_count)
                    except PermissionError as exc:
                        decision["finish"] = True
                        decision["stopping_reason"] = "policy_blocked"
                        decision["assessment"] += f"；{exc}"
                        policy_blocked = True
                        break
                    mutation_intent.add(action["tool"])
                    mutation_count += 1
                    result = self._execute(
                        session_id,
                        plan,
                        results,
                        action["tool"],
                        action["arguments"],
                        f"第 {round_number} 轮调整：{action['reason']}",
                        allow_changes=True,
                    )
                    if result.success:
                        changed.add(action["tool"])
                        iteration["executed_actions"].append(
                            {"tool": action["tool"], "arguments": action["arguments"], "result": result.data}
                        )
                    else:
                        action_failed = True
                        break
                if action_failed or policy_blocked:
                    break
                time.sleep(0.35)
        finally:
            if original is not None:
                restore_actions = [
                    ("set_channel_scale", {"channel": channel, "volts_per_div": original["scale_v_per_div"]}),
                    ("set_timebase_scale", {"seconds_per_div": original["timebase_s_per_div"]}),
                    ("set_trigger_level", {"level_v": original["trigger_level_v"]}),
                ]
                for tool, arguments in restore_actions:
                    if not mutation_intent:
                        continue
                    result = self._execute(
                        session_id,
                        plan,
                        results,
                        tool,
                        arguments,
                        "恢复闭环任务开始前的设备设置",
                        allow_changes=True,
                    )
                    if not result.success:
                        restore_errors.append(result.error or f"{tool} 恢复失败")
                verification = self._execute(
                    session_id,
                    plan,
                    results,
                    "get_device_status",
                    {},
                    "验证闭环任务结束后设备设置已恢复",
                    allow_changes=False,
                )
                if not verification.success:
                    restore_errors.append("设备设置恢复后无法完成读回验证，恢复状态未知")
                elif not _restored(_settings(verification.data["status"], channel), original):
                    restore_errors.append("设备设置恢复验证不一致")

        critical_results = [item for item in results if item.tool != "capture_screen"]
        optional_failures = [item for item in results if item.tool == "capture_screen" and not item.success]
        execution_success = bool(critical_results) and all(item.success for item in critical_results)
        final_observation = iterations[-1]["observation"] if iterations else {}
        quality = assess_quality(iterations)
        scientific_success = bool(iterations) and quality["score"] >= 0.68
        success = execution_success and scientific_success and not restore_errors
        conclusion = _conclusion(iterations, quality, restore_errors)
        final_hypotheses = iterations[-1].get("hypotheses", []) if iterations else []
        final_hypothesis = final_hypotheses[0] if final_hypotheses else {}
        stopping_reason = (
            str(iterations[-1].get("decision", {}).get("stopping_reason") or "experiment_budget_exhausted")
            if iterations
            else "observation_failed"
        )
        if action_failed:
            stopping_reason = "action_failed"
        if policy_blocked:
            stopping_reason = "policy_blocked"
        if observation_failed:
            stopping_reason = "observation_failed"
        if restore_errors:
            stopping_reason = "restore_failed"
        if scientific_success and stopping_reason == "experiment_budget_exhausted":
            stopping_reason = "scientific_goal_met"
        objective = build_objective(channel, final_observation, quality)
        stop = build_stop(stopping_reason, len(iterations), scientific_success)
        base_analysis = analyze_results(results)
        if optional_failures:
            base_analysis.setdefault("warnings", []).append(
                f"{len(optional_failures)} 次屏幕截图失败；结构化测量和波形仍可继续用于科学判断"
            )
        base_analysis.update(
            {
                "conclusion": conclusion,
                "execution_success": execution_success,
                "scientific_success": scientific_success,
                "settings_restored": not restore_errors,
                "restoration_status": "restored" if not restore_errors else "failed",
                "adaptive_change_lease": {
                    "granted": lease is not None,
                    "channel": channel,
                    "max_mutations": lease.max_mutations if lease else 0,
                    "used_mutations": mutation_count,
                },
                "quality": quality,
                "experiment_mode": "hypothesis_driven",
                "objective": objective,
                "stop": stop,
                "prior_memory": prior_memory,
                "adaptive_iterations": iterations,
                "final_observation": final_observation,
                "final_hypotheses": final_hypotheses,
                "final_hypothesis": final_hypothesis,
                "stopping_reason": stopping_reason,
            }
        )
        summary = (
            f"闭环分析完成 {len(iterations)} 轮，结论置信度 {quality['score']:.0%}，设备设置已恢复。"
            if not restore_errors
            else f"闭环分析完成，但设备设置恢复存在问题：{'; '.join(restore_errors)}"
        )
        plan.summary = f"闭环实际执行 {len(plan.steps)} 个受控步骤，共 {len(iterations)} 轮"
        task_result = TaskResult(
            session_id=session_id,
            request=request,
            success=success,
            plan=plan,
            results=results,
            started_at=started_at,
            finished_at=now_iso(),
            summary=summary,
            analysis=base_analysis,
        )
        output_path = self.output_dir / f"{session_id}.json"
        report_path = self.output_dir / f"{session_id}.html"
        task_result.output_path = str(output_path)
        task_result.report_path = str(report_path)
        memory_record: dict[str, Any] | None = None
        try:
            memory_record = self.memory.record(
                {
                    "session_id": session_id,
                    "channel": channel,
                    "request": request,
                    "final_hypothesis": final_hypothesis,
                    "quality": quality,
                    "execution_success": execution_success,
                    "scientific_success": scientific_success,
                    "settings_restored": not restore_errors,
                    "rounds": len(iterations),
                    "stopping_reason": stopping_reason,
                    "evidence_fingerprint": evidence_fingerprint(final_observation),
                }
            )
        except OSError as exc:
            base_analysis.setdefault("warnings", []).append(f"实验记忆写入失败：{exc}")
        base_analysis["memory_record"] = memory_record
        save_json(output_path, task_result.to_dict())
        save_html_report(task_result, report_path)
        return task_result

    def _execute(
        self,
        session_id: str,
        plan: TaskPlan,
        results: list[ToolResult],
        tool: str,
        arguments: dict[str, Any],
        reason: str,
        *,
        allow_changes: bool,
    ) -> ToolResult:
        plan.steps.append(PlanStep(tool, arguments, reason))
        registry = self.change_tools if allow_changes else self.read_tools
        started = now_iso()
        spec = registry.spec(tool)
        try:
            data = registry.execute(tool, arguments)
            result = ToolResult(tool, arguments, True, started, now_iso(), data=data, risk=spec.risk)
        except Exception as exc:
            result = ToolResult(
                tool,
                arguments,
                False,
                started,
                now_iso(),
                error=f"{type(exc).__name__}: {exc}",
                risk=spec.risk,
            )
        results.append(result)
        path = self.output_dir.parent / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"session_id": session_id, **result.to_dict()}, ensure_ascii=False, default=str) + "\n")
        return result


class AdaptiveDecisionEngine:
    def __init__(self, planner: Any, change_tools: ToolRegistry) -> None:
        candidate = getattr(planner, "primary", planner)
        self.client = getattr(candidate, "client", None)
        self.provider = getattr(candidate, "provider", "rules")
        self.model = getattr(candidate, "model", None)
        self.change_tools = change_tools
        self.name = f"{self.provider}:{self.model}" if self.model else "deterministic"

    def decide(
        self,
        *,
        request: str,
        round_number: int,
        max_rounds: int,
        settings: dict[str, float],
        observation: dict[str, Any],
        hypotheses: list[dict[str, Any]],
        prior_memory: list[dict[str, Any]],
    ) -> dict[str, Any]:
        local = deterministic_decision(round_number, max_rounds, settings, observation, hypotheses)
        if self.client is None:
            return local
        started = time.perf_counter()
        try:
            options: dict[str, Any] = {}
            if self.provider == "deepseek":
                options["extra_body"] = {"thinking": {"type": "disabled"}}
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是示波器闭环实验控制器。根据当前设置、测量值、波形统计和上一轮变化，"
                            "维护多个可证伪假设，选择下一项能最大程度区分假设的实验，并只在必要时选择"
                            "垂直档位、时基和触发电平调整。不得请求原始 SCPI，不得开关通道，不得修改其他设置。"
                            "实验轮数有严格预算，优先获得可验证结论，并明确停止原因。"
                            "无稳定频率可能代表直流、单次阶跃或接线问题，不得把 null 当作 0。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "goal": request,
                                "round": round_number,
                                "max_rounds": max_rounds,
                                "settings": settings,
                                "observation": observation,
                                "ranked_hypotheses": hypotheses,
                                "recent_factual_memory": prior_memory,
                                "local_guardrail_suggestion": local,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                tools=[_decision_tool()],
                tool_choice={"type": "function", "function": {"name": "submit_signal_decision"}},
                temperature=0,
                **options,
            )
            arguments = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
            decision_settings = {
                **settings,
                "signal_median_v": float(observation["waveform"]["median_v"]),
            }
            model_decision = _model_decision(
                arguments,
                decision_settings,
                observation,
                hypotheses,
                round_number,
                max_rounds,
            )
            model_decision["source"] = "model"
            model_decision["planning"] = {
                "provider": self.provider,
                "model": self.model,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "response_id": getattr(response, "id", None),
                "usage": _usage(getattr(response, "usage", None)),
            }
            return merge_guardrails(model_decision, local, decision_settings)
        except Exception as exc:
            local["source"] = "deterministic_fallback"
            local["planning"] = {
                "provider": self.provider,
                "model": self.model,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
            return local


def build_observation(
    settings: dict[str, float],
    measurement_data: dict[str, Any],
    waveform_data: dict[str, Any],
    *,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    measurements = measurement_data.get("measurements", {})
    values = _read_voltages(Path(str(waveform_data["csv_path"])))
    ordered = sorted(values)
    count = len(ordered)
    p05 = _percentile(ordered, 0.05)
    p95 = _percentile(ordered, 0.95)
    median = statistics.median(ordered)
    mean = statistics.fmean(ordered)
    minimum = ordered[0]
    maximum = ordered[-1]
    robust_span = p95 - p05
    full_span = maximum - minimum
    preamble = waveform_data.get("capture", {}).get("preamble", {})
    quantization = abs(float(preamble.get("y_increment") or 0.0))
    x_increment = abs(float(preamble.get("x_increment") or 0.0))
    frequency = _number(measurements.get("FREQUENCY"))
    vpp = _number(measurements.get("VPP"))
    rms = _number(measurements.get("RMS"))
    vmax = _number(measurements.get("VMAX"))
    vmin = _number(measurements.get("VMIN"))
    threshold = (p05 + p95) / 2
    transition_count = sum(
        1 for left, right in zip(values, values[1:]) if (left < threshold) != (right < threshold)
    )
    display_minimum = settings["offset_v"] - settings["scale_v_per_div"] * 4.0
    display_maximum = settings["offset_v"] + settings["scale_v_per_div"] * 4.0
    clipping_tolerance = max(quantization * 0.51, settings["scale_v_per_div"] * 0.002)
    clipping_fraction = sum(
        1
        for value in values
        if value <= display_minimum + clipping_tolerance or value >= display_maximum - clipping_tolerance
    ) / count
    clipping_suspected = clipping_fraction >= 0.02 and full_span >= settings["scale_v_per_div"] * 7.5
    observed_duration = x_increment * max(count - 1, 0)
    observed_cycles = observed_duration * frequency if frequency else None
    consistency_ratio = (
        abs(vpp - robust_span) / max(abs(vpp), abs(robust_span), 1e-9)
        if vpp is not None
        else None
    )
    vertical_span_divisions = robust_span / settings["scale_v_per_div"] if settings["scale_v_per_div"] else None
    signal_class = _classify(
        frequency,
        vpp,
        rms,
        median,
        robust_span,
        quantization,
        vertical_span_divisions,
        transition_count,
        clipping_suspected,
    )
    issues: list[str] = []
    if quantization and quantization > max(0.02, robust_span / 25 if robust_span else 0.02):
        issues.append("垂直分辨率偏粗")
    if consistency_ratio is not None and consistency_ratio > 0.35:
        issues.append("自动峰峰值与波形统计不一致")
    if frequency is None:
        issues.append("没有稳定频率读数")
    if quantization <= 0:
        issues.append("缺少量化步进元数据")
    if clipping_suspected:
        issues.append("波形可能触及显示边界或削顶")
    if frequency and observed_cycles is not None and observed_cycles < 2:
        issues.append("当前时间窗口不足两个周期")
    stability: dict[str, Any] | None = None
    if previous:
        stability = {
            "median_delta_v": abs(median - float(previous["waveform"]["median_v"])),
            "rms_delta_v": abs((rms or 0.0) - float(previous["measurements"].get("rms_v") or 0.0)),
            "class_unchanged": signal_class == previous.get("signal_class"),
        }
    return {
        "measurements": {
            "frequency_hz": frequency,
            "vpp_v": vpp,
            "rms_v": rms,
            "vmax_v": vmax,
            "vmin_v": vmin,
        },
        "waveform": {
            "points": count,
            "minimum_v": minimum,
            "maximum_v": maximum,
            "p05_v": p05,
            "p95_v": p95,
            "median_v": median,
            "mean_v": mean,
            "robust_span_v": robust_span,
            "full_span_v": full_span,
            "quantization_v": quantization,
            "transition_count": transition_count,
            "vertical_span_divisions": vertical_span_divisions,
            "observed_duration_s": observed_duration,
            "observed_cycles": observed_cycles,
            "clipping_fraction": clipping_fraction,
            "clipping_suspected": clipping_suspected,
        },
        "signal_class": signal_class,
        "consistency_ratio": consistency_ratio,
        "stability": stability,
        "issues": issues,
        "validity": {
            "quantization_known": quantization > 0,
            "resolution_bins": robust_span / quantization if quantization > 0 else None,
            "vertical_occupancy_ok": bool(vertical_span_divisions is not None and vertical_span_divisions >= 0.5),
            "not_clipped": not clipping_suspected,
            "period_coverage_ok": frequency is None or (observed_cycles is not None and observed_cycles >= 2),
            "measurement_waveform_consistent": consistency_ratio is not None and consistency_ratio <= 0.35,
        },
    }


def generate_hypotheses(
    observation: dict[str, Any],
    previous_iterations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    measurements = observation["measurements"]
    waveform = observation["waveform"]
    frequency = measurements.get("frequency_hz")
    rms = measurements.get("rms_v")
    span = abs(float(waveform.get("robust_span_v") or 0.0))
    median = float(waveform.get("median_v") or 0.0)
    quantization = abs(float(waveform.get("quantization_v") or 0.0))
    divisions = abs(float(waveform.get("vertical_span_divisions") or 0.0))
    transitions = int(waveform.get("transition_count") or 0)
    bins = span / quantization if quantization > 0 else None
    stability = observation.get("stability") or {}

    candidates: list[dict[str, Any]] = []

    def add(
        hypothesis_id: str,
        label: str,
        score: float,
        supporting: list[str],
        contradicting: list[str],
        unresolved: list[str],
    ) -> None:
        candidates.append(
            {
                "id": hypothesis_id,
                "label": label,
                "score": round(max(0.0, min(score, 1.0)), 3),
                "supporting_evidence": supporting,
                "contradicting_evidence": contradicting,
                "unresolved": unresolved,
            }
        )

    resolved_dc_level = quantization > 0 and abs(median) / quantization >= 5 and span <= quantization * 2
    resolution_bad = quantization <= 0 or bins is None or bins < 5 or divisions < 0.5
    resolution_support = []
    if quantization <= 0:
        resolution_support.append("波形没有可用的量化步进元数据")
    else:
        resolution_support.append(f"信号覆盖约 {bins:.2f} 个量化台阶、{divisions:.2f} 格")
    add(
        "acquisition_limited",
        "当前采集设置或物理条件不足以形成可靠信号结论",
        0.35 if resolution_bad and resolved_dc_level else 0.92 if resolution_bad else 0.12,
        resolution_support if resolution_bad else [],
        ["垂直分辨率已达到最低证据门槛"] if not resolution_bad else [],
        ["改善分辨率后信号类别是否稳定"] if resolution_bad else [],
    )

    periodic_score = 0.93 if frequency is not None and frequency > 0 and not resolution_bad else 0.12
    periodic_support = [f"自动频率测量为 {frequency:.6g} Hz"] if frequency else []
    periodic_contra = []
    if frequency is None:
        periodic_contra.append("没有稳定频率读数")
    if resolution_bad:
        periodic_contra.append("波形分辨率不足，频率读数不能单独支持周期结论")
    add(
        "periodic",
        "稳定周期信号",
        periodic_score,
        periodic_support,
        periodic_contra,
        ["跨设置复测频率和幅度是否保持"] if periodic_score >= 0.5 else [],
    )

    dc_dominance = abs(median) / max(span, quantization, 1e-12)
    rms_matches_dc = rms is not None and abs(abs(float(rms)) - abs(median)) <= max(abs(median) * 0.25, quantization * 3, 0.002)
    dc_score = 0.15
    if frequency is None and dc_dominance >= 3 and abs(median) >= max(quantization * 5, 0.002):
        dc_score = 0.82 + (0.08 if rms_matches_dc else 0.0)
    if stability and stability.get("median_delta_v", float("inf")) <= max(quantization * 5, abs(median) * 0.05, 0.002):
        dc_score += 0.05
    add(
        "dc_or_slow",
        "直流或缓慢变化信号",
        dc_score,
        [
            f"中位值 {median:.6g} V 明显大于波动范围 {span:.6g} V",
            "RMS 与直流电平相符" if rms_matches_dc else "",
        ] if dc_score >= 0.5 else [],
        ["存在稳定频率读数"] if frequency else [],
        ["扩大时间窗口并复测电平稳定性"] if dc_score >= 0.5 else [],
    )

    step_score = 0.78 if frequency is None and not resolution_bad and 1 <= transitions <= 12 else 0.1
    add(
        "transient_or_step",
        "瞬态、阶跃或接触变化",
        step_score,
        [f"波形只有 {transitions} 次阈值跨越"] if step_score >= 0.5 else [],
        ["存在稳定周期"] if frequency else [],
        ["扩大观察窗口后事件是否重复"] if step_score >= 0.5 else [],
    )

    noise_score = 0.76 if frequency is None and not resolution_bad and transitions > 12 else 0.12
    if stability and stability.get("class_unchanged") and noise_score >= 0.5:
        noise_score += 0.08
    add(
        "aperiodic_noise",
        "非周期噪声或不稳定活动",
        noise_score,
        [f"无稳定频率且存在 {transitions} 次阈值跨越"] if noise_score >= 0.5 else [],
        ["存在稳定频率"] if frequency else [],
        ["同设置复测统计分布是否稳定"] if noise_score >= 0.5 else [],
    )

    unresolved_score = 0.5 if max(item["score"] for item in candidates) < 0.7 else 0.08
    add(
        "unresolved",
        "现有证据无法区分信号类别",
        unresolved_score,
        ["多个候选假设分数接近"] if unresolved_score >= 0.5 else [],
        [],
        ["需要新的可区分实验"],
    )

    candidates.sort(key=lambda item: item["score"], reverse=True)
    for index, item in enumerate(candidates):
        item["status"] = "leading" if index == 0 else "rejected" if item["score"] < 0.2 else "active"
        item["rank"] = index + 1
        item["supporting_evidence"] = [value for value in item["supporting_evidence"] if value]
    return candidates


def evidence_fingerprint(observation: dict[str, Any]) -> dict[str, Any]:
    if not observation:
        return {}
    measurements = observation.get("measurements", {})
    waveform = observation.get("waveform", {})
    return {
        "frequency_hz": measurements.get("frequency_hz"),
        "vpp_v": measurements.get("vpp_v"),
        "rms_v": measurements.get("rms_v"),
        "median_v": waveform.get("median_v"),
        "robust_span_v": waveform.get("robust_span_v"),
        "quantization_v": waveform.get("quantization_v"),
        "vertical_span_divisions": waveform.get("vertical_span_divisions"),
        "signal_class": observation.get("signal_class"),
    }


def build_evidence_delta(
    observation: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    if not previous:
        return {"baseline": True, "metrics": []}
    current_measurements = observation.get("measurements", {})
    prior_measurements = previous.get("measurements", {})
    current_waveform = observation.get("waveform", {})
    prior_waveform = previous.get("waveform", {})
    metrics: list[dict[str, Any]] = []
    for name, current, before in (
        ("frequency_hz", current_measurements.get("frequency_hz"), prior_measurements.get("frequency_hz")),
        ("vpp_v", current_measurements.get("vpp_v"), prior_measurements.get("vpp_v")),
        ("rms_v", current_measurements.get("rms_v"), prior_measurements.get("rms_v")),
        ("median_v", current_waveform.get("median_v"), prior_waveform.get("median_v")),
        ("robust_span_v", current_waveform.get("robust_span_v"), prior_waveform.get("robust_span_v")),
        ("quantization_v", current_waveform.get("quantization_v"), prior_waveform.get("quantization_v")),
        ("vertical_span_divisions", current_waveform.get("vertical_span_divisions"), prior_waveform.get("vertical_span_divisions")),
    ):
        trend = "unknown"
        if isinstance(current, (int, float)) and isinstance(before, (int, float)):
            tolerance = max(abs(float(before)) * 0.02, 1e-12)
            delta = float(current) - float(before)
            trend = "increased" if delta > tolerance else "decreased" if delta < -tolerance else "stable"
        metrics.append({"name": name, "before": before, "after": current, "trend": trend})
    return {
        "baseline": False,
        "class_before": previous.get("signal_class"),
        "class_after": observation.get("signal_class"),
        "metrics": metrics,
    }


def build_objective(channel: int, observation: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    validity = observation.get("validity", {}) if observation else {}
    waveform = observation.get("waveform", {}) if observation else {}
    bins = validity.get("resolution_bins")
    divisions = waveform.get("vertical_span_divisions")
    stability = observation.get("stability") if observation else None
    criteria = [
        {
            "id": "valid_observation",
            "label": "目标通道取得结构化测量和波形",
            "met": bool(observation),
            "observed": "已取得" if observation else "未取得",
        },
        {
            "id": "vertical_resolution",
            "label": "变化量至少覆盖 5 个量化台阶和 0.5 格，或直流电平可分辨",
            "met": bool(quality.get("score", 0.0) >= 0.5 and validity.get("quantization_known")),
            "observed": f"{bins if bins is not None else '未知'} 个台阶，{divisions if divisions is not None else '未知'} 格",
        },
        {
            "id": "not_clipped",
            "label": "波形未触及显示边界",
            "met": bool(validity.get("not_clipped", False)),
            "observed": f"边界点比例 {float(waveform.get('clipping_fraction') or 0.0):.1%}" if observation else "无观测",
        },
        {
            "id": "replicated",
            "label": "关键类别或统计经过独立复测",
            "met": bool(stability and stability.get("class_unchanged")),
            "observed": "分类保持" if stability and stability.get("class_unchanged") else "尚未复现",
        },
    ]
    return {
        "statement": f"把 CH{channel} 信号测到足以形成可复核的分类",
        "channel": channel,
        "success_criteria": criteria,
    }


def build_stop(reason: str, round_number: int, scientific_success: bool) -> dict[str, Any]:
    descriptions = {
        "scientific_goal_met": ("conclusion", "证据质量和复测达到本地科学门槛", False, []),
        "physical_setup_required": ("evidence_limit", "剩余不确定性需要改善探头、接地、接触或外部参考", True, ["检查信号地线", "检查探头或表笔接触", "确认被测节点"]),
        "experiment_budget_exhausted": ("budget", "实验轮次预算用完，仍未达到可信结论门槛", True, ["改善物理条件后重试"]),
        "policy_blocked": ("policy", "安全策略或任务租约不允许继续", True, ["确认通道状态和显式授权"]),
        "action_failed": ("device_error", "设备修改未能确认，已立即停止并进入恢复", True, ["检查设备连接和错误队列"]),
        "restore_failed": ("restore", "设备设置恢复失败或无法验证", False, ["人工检查设备当前设置"]),
        "observation_failed": ("device_error", "没有取得完整有效观测", True, ["检查设备连接"]),
    }
    category, description, retryable, requirements = descriptions.get(
        reason,
        ("inconclusive", "实验停止但原因未归类", not scientific_success, []),
    )
    return {
        "code": reason,
        "category": category,
        "round": round_number,
        "reason": description,
        "retryable": retryable,
        "requirements": requirements,
    }


def deterministic_decision(
    round_number: int,
    max_rounds: int,
    settings: dict[str, float],
    observation: dict[str, Any],
    hypotheses: list[dict[str, Any]],
) -> dict[str, Any]:
    leading = hypotheses[0]
    if round_number >= max_rounds:
        return {
            "hypothesis_id": leading["id"],
            "hypothesis": leading["label"],
            "assessment": "达到闭环轮次上限，使用现有证据形成结论",
            "confidence": leading["score"],
            "finish": True,
            "actions": [],
            "experiment": _experiment(
                "stop_budget",
                "实验预算已经用完",
                ["保留未解决假设并报告需要的物理条件"],
                "不再修改设备",
            ),
            "stopping_reason": "experiment_budget_exhausted",
            "source": "deterministic",
            "planning": {"duration_ms": 0.0},
        }
    waveform = observation["waveform"]
    measurements = observation["measurements"]
    actions: list[dict[str, Any]] = []
    span = max(float(waveform["robust_span_v"]), float(measurements.get("vpp_v") or 0.0), 1e-6)
    offset_distance = abs(float(waveform["median_v"]) - settings["offset_v"])
    ideal_scale = max(span / 6.0, offset_distance / 3.5, 0.001)
    target_scale = _ceiling_ladder(ideal_scale, SCALE_LADDER)
    current_scale = settings["scale_v_per_div"]
    frequency = measurements.get("frequency_hz")
    current_timebase = settings["timebase_s_per_div"]

    experiment = _experiment(
        "repeat_baseline",
        "在相同设置下复测，检查关键统计是否可重复",
        ["若分类和关键统计稳定，则提高领先假设可信度", "若显著漂移，则支持不稳定或物理接触问题"],
        "相邻两轮关键统计落在量化与幅度容差内",
    )
    stopping_reason = "continue_experiment"

    if observation.get("validity", {}).get("not_clipped") is False:
        target_scale = _ceiling_ladder(max(span / 6.0, current_scale * 2.0), SCALE_LADDER)
        if target_scale > current_scale:
            return {
                "hypothesis_id": "acquisition_limited",
                "hypothesis": "当前垂直档位导致波形削顶",
                "assessment": "先单独增大量程，确认削顶消失后再判断幅度和信号类别",
                "confidence": 0.95,
                "finish": False,
                "actions": [{
                    "tool": "set_channel_scale",
                    "arguments": {"channel": int(settings["channel"]), "volts_per_div": target_scale},
                    "reason": "波形触及显示边界，单独增大量程以消除削顶",
                }],
                "experiment": _experiment("clipping_recovery_test", "只增大垂直档位，验证波形边界是否来自削顶", ["真实削顶应在增大量程后消失", "幅度统计恢复后才允许分类"], "边界点比例下降且幅度不再等于满屏范围"),
                "stopping_reason": "continue_experiment",
                "source": "deterministic",
                "planning": {"duration_ms": 0.0},
            }

    if leading["id"] == "acquisition_limited":
        if frequency and frequency > 0:
            target_timebase = _nearest_ladder((1.0 / float(frequency)) / 4.0, TIMEBASE_LADDER)
            if not math.isclose(target_timebase, current_timebase, rel_tol=1e-6):
                actions = [{
                    "tool": "set_timebase_scale",
                    "arguments": {"seconds_per_div": target_timebase},
                    "reason": "已有频率候选但当前窗口不足，先只改变时基验证周期覆盖",
                }]
                experiment = _experiment(
                    "period_coverage_test",
                    "只改变时基，检验频率候选能否在至少两个完整周期上复现",
                    ["真实周期信号应在合适窗口中形成可分辨的周期波形", "错误频率读数不会得到波形支持"],
                    "观察至少两个周期且垂直分辨率合格",
                )
                return {
                    "hypothesis_id": leading["id"],
                    "hypothesis": leading["label"],
                    "assessment": "优先验证时间窗口，而不同时改变垂直档位",
                    "confidence": leading["score"],
                    "finish": False,
                    "actions": actions,
                    "experiment": experiment,
                    "stopping_reason": "continue_experiment",
                    "source": "deterministic",
                    "planning": {"duration_ms": 0.0},
                }
        if current_scale / target_scale >= 1.5:
            actions = [{
                "tool": "set_channel_scale",
                "arguments": {"channel": int(settings["channel"]), "volts_per_div": target_scale},
                "reason": "单独提高垂直分辨率，检验微小活动是否随量化步进缩小而保持",
            }]
            experiment = _experiment(
                "vertical_resolution_test",
                "只改变垂直档位，区分真实微小信号与粗量化伪影",
                ["真实信号应在更细档位下覆盖更多量化台阶", "量化伪影不会形成稳定、可重复的波形统计"],
                "覆盖至少 5 个量化台阶且至少 0.5 格",
            )
        elif round_number >= 2:
            return {
                "hypothesis_id": leading["id"],
                "hypothesis": leading["label"],
                "assessment": "已经没有能够安全提升信息量的垂直调整",
                "confidence": leading["score"],
                "finish": True,
                "actions": [],
                "experiment": _experiment("stop_evidence_limit", "剩余不确定性需要改善物理接线或外部参考", ["检查接地、接触和探头"], "物理条件改善后重试"),
                "stopping_reason": "physical_setup_required",
                "source": "deterministic",
                "planning": {"duration_ms": 0.0},
            }
    elif leading["id"] == "periodic":
        target_timebase = _nearest_ladder((1.0 / float(frequency)) / 4.0, TIMEBASE_LADDER)
        if not math.isclose(target_timebase, current_timebase, rel_tol=1e-6):
            actions = [{
                "tool": "set_timebase_scale",
                "arguments": {"seconds_per_div": target_timebase},
                "reason": "单独调整观察窗口，使屏幕覆盖约四个周期并复核频率",
            }]
            experiment = _experiment("period_window_test", "只改变时基，检查周期特征能否跨设置保持", ["频率应保持，波形应覆盖多个完整周期"], "频率跨设置一致且波形分辨率合格")
        elif round_number >= 2 and _stable_enough(observation):
            return _finish_decision(leading, "周期特征已经跨轮复现", "scientific_goal_met")
    elif leading["id"] == "dc_or_slow":
        target_timebase = _nearest_ladder(current_timebase * 10.0, TIMEBASE_LADDER)
        if round_number == 1 and not math.isclose(target_timebase, current_timebase, rel_tol=1e-6):
            actions = [{
                "tool": "set_timebase_scale",
                "arguments": {"seconds_per_div": target_timebase},
                "reason": "单独扩大观察窗口，区分稳定直流与缓慢变化",
            }]
            experiment = _experiment("slow_time_window_test", "只改变时基，检验电平在更长窗口内是否保持", ["稳定直流的中位值与 RMS 应保持", "慢变信号会显示更大的跨度或漂移"], "长窗口与复测均保持同一电平")
        elif round_number >= 2 and _stable_enough(observation):
            return _finish_decision(leading, "直流电平和小波动已经复测稳定", "scientific_goal_met")
    elif leading["id"] == "transient_or_step":
        target_timebase = _nearest_ladder(current_timebase * 10.0, TIMEBASE_LADDER)
        if not math.isclose(target_timebase, current_timebase, rel_tol=1e-6):
            actions = [{
                "tool": "set_timebase_scale",
                "arguments": {"seconds_per_div": target_timebase},
                "reason": "单独扩大观察窗口，检验阶跃是否重复或只是偶发事件",
            }]
            experiment = _experiment("transient_window_test", "只改变时基，区分重复瞬态与单次接触变化", ["重复事件会在长窗口中再次出现", "单次事件不会稳定复现"], "事件形态在独立窗口中可复现")
        elif round_number >= 2 and _stable_enough(observation):
            return _finish_decision(leading, "瞬态类别已经复测，但不推断其物理原因", "scientific_goal_met")
    elif leading["id"] == "aperiodic_noise":
        if current_scale / target_scale >= 1.5:
            actions = [{
                "tool": "set_channel_scale",
                "arguments": {"channel": int(settings["channel"]), "volts_per_div": target_scale},
                "reason": "单独改善垂直分辨率，复核噪声分布而不伪造频率",
            }]
            experiment = _experiment("noise_resolution_test", "只改变垂直档位，检查非周期统计是否保持", ["真实噪声分布在更细档位下仍应保持相近 RMS 与跨度"], "两轮分布统计一致且无稳定频率")
        elif round_number >= 2 and _stable_enough(observation):
            return _finish_decision(leading, "非周期统计已经复测稳定", "scientific_goal_met")

    return {
        "hypothesis_id": leading["id"],
        "hypothesis": leading["label"],
        "assessment": "选择一项单变量实验以最大化下一轮信息增益" if actions else "保持设置复测，检查证据可重复性",
        "confidence": leading["score"],
        "finish": False,
        "actions": actions,
        "experiment": experiment,
        "stopping_reason": stopping_reason,
        "source": "deterministic",
        "planning": {"duration_ms": 0.0},
    }


def _experiment(
    experiment_id: str,
    question: str,
    expected_outcomes: list[str],
    success_criterion: str,
) -> dict[str, Any]:
    return {
        "id": experiment_id,
        "question": question,
        "expected_outcomes": expected_outcomes,
        "success_criterion": success_criterion,
        "single_variable": True,
    }


def _stable_enough(observation: dict[str, Any]) -> bool:
    stability = observation.get("stability")
    if not stability:
        return False
    waveform = observation.get("waveform", {})
    quantization = abs(float(waveform.get("quantization_v") or 0.0))
    span = abs(float(waveform.get("robust_span_v") or 0.0))
    limit = max(quantization * 5, span * 0.25, 0.002)
    return bool(stability.get("class_unchanged")) and float(stability.get("median_delta_v") or 0.0) <= limit


def _finish_decision(leading: dict[str, Any], assessment: str, stopping_reason: str) -> dict[str, Any]:
    return {
        "hypothesis_id": leading["id"],
        "hypothesis": leading["label"],
        "assessment": assessment,
        "confidence": leading["score"],
        "finish": True,
        "actions": [],
        "experiment": _experiment("replication_complete", "复测已经满足停止条件", [assessment], "领先假设有独立复测支持"),
        "stopping_reason": stopping_reason,
        "source": "deterministic",
        "planning": {"duration_ms": 0.0},
    }


def merge_guardrails(model: dict[str, Any], local: dict[str, Any], settings: dict[str, float]) -> dict[str, Any]:
    # The model may rank hypotheses and explain the experiment, but all device
    # parameters and the final stop decision remain local and deterministic.
    model["raw_actions"] = list(model.get("actions", []))
    model["actions"] = list(local.get("actions", []))[:1]
    model["experiment"] = local.get("experiment")
    model["stopping_reason"] = local.get("stopping_reason")
    model["finish"] = bool(local.get("finish"))
    model["hypothesis_id"] = local.get("hypothesis_id")
    if not model.get("hypothesis"):
        model["hypothesis"] = local.get("hypothesis")
    model["confidence"] = min(float(model.get("confidence", 0.5)), float(local.get("confidence", 0.5)) + 0.1)
    model["source"] = "model_explanation+local_experiment_controller"
    return model


def assess_quality(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    if not iterations or not iterations[-1].get("observation"):
        return {"score": 0.0, "level": "无结果", "reasons": ["没有完成有效观测"]}
    observation = iterations[-1]["observation"]
    waveform = observation["waveform"]
    score = 0.3
    reasons = ["设备通信和数据采集成功"]
    quantization = float(waveform["quantization_v"] or 0.0)
    span = float(waveform["robust_span_v"] or 0.0)
    resolution_bins = span / quantization if quantization > 0 else None
    vertical_divisions = float(waveform.get("vertical_span_divisions") or 0.0)
    leading_id = str((iterations[-1].get("hypotheses") or [{}])[0].get("id") or "")
    dc_level_bins = abs(float(waveform.get("median_v") or 0.0)) / quantization if quantization > 0 else 0.0
    resolution_ok = bool(
        (resolution_bins is not None and resolution_bins >= 5 and vertical_divisions >= 0.5)
        or (leading_id == "dc_or_slow" and dc_level_bins >= 5)
    )
    if resolution_ok:
        score += 0.2
        reasons.append("最终垂直分辨率或直流电平分辨率可接受")
    else:
        bins_text = "未知" if resolution_bins is None else f"{resolution_bins:.1f}"
        reasons.append(f"最终信号仅覆盖约 {bins_text} 个量化台阶、{vertical_divisions:.2f} 格，分辨率证据不足")
    consistency = observation.get("consistency_ratio")
    if consistency is not None and consistency <= 0.35 and resolution_bins is not None and resolution_bins >= 3:
        score += 0.2
        reasons.append("自动测量与波形统计基本一致")
    if observation.get("signal_class") not in {"未解析", "低于有效分辨率的微小活动"}:
        score += 0.15
        reasons.append(f"信号被分类为{observation['signal_class']}")
    stability = observation.get("stability")
    stability_limit = max(quantization * 5, span * 0.5, 0.002)
    if stability and stability["class_unchanged"] and stability["median_delta_v"] <= stability_limit:
        score += 0.15
        reasons.append("相邻两轮观测基本稳定")
    elif stability:
        reasons.append(f"相邻两轮中位值变化 {stability['median_delta_v']:.6g} V，超过稳定性阈值 {stability_limit:.6g} V")
    elif len(iterations) == 1:
        score += 0.05
        reasons.append("只有一轮观测，稳定性证据有限")
    validity = observation.get("validity", {})
    if not validity.get("not_clipped", True):
        score = min(score, 0.45)
        reasons.append("波形触及显示边界，幅度结论无效")
    if quantization <= 0:
        score = min(score, 0.45)
        reasons.append("量化步进未知，不能获得分辨率加分")
    if observation.get("measurements", {}).get("frequency_hz") is not None and not validity.get("period_coverage_ok", False):
        score = min(score, 0.6)
        reasons.append("观察窗口不足两个周期，频率结论未完成独立验证")
    score = min(score, 1.0)
    level = "高" if score >= 0.85 else "中" if score >= 0.68 else "低"
    return {"score": round(score, 3), "level": level, "reasons": reasons}


def _conclusion(iterations: list[dict[str, Any]], quality: dict[str, Any], restore_errors: list[str]) -> str:
    if not iterations or not iterations[-1].get("observation"):
        return "闭环任务未取得有效观测。"
    final = iterations[-1]["observation"]
    measurement = final["measurements"]
    waveform = final["waveform"]
    frequency_text = (
        f"频率约 {measurement['frequency_hz']:.6g} Hz"
        if measurement.get("frequency_hz") is not None
        else "没有稳定周期，因此频率无有效值"
    )
    restore_text = "设备设置已恢复" if not restore_errors else f"设备恢复异常：{'; '.join(restore_errors)}"
    quality_text = (
        "现有证据足以形成可信分类"
        if quality["score"] >= 0.68
        else "现有接线或分辨率不足，不能形成可信电气结论；应检查信号地线、接触和探头后重测"
    )
    return (
        f"闭环完成 {len(iterations)} 轮，信号判断为“{final['signal_class']}”，置信度 {quality['level']}（{quality['score']:.0%}）。"
        f"{frequency_text}；RMS {measurement.get('rms_v')} V；波形中位值 {waveform['median_v']:.6g} V，"
        f"稳健幅度范围 {waveform['robust_span_v']:.6g} V。{quality_text}。{restore_text}。"
    )


def _model_decision(
    arguments: dict[str, Any],
    settings: dict[str, float],
    observation: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    round_number: int,
    max_rounds: int,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    channel = int(settings["channel"])
    scale = arguments.get("vertical_scale_v_per_div")
    if scale is not None:
        actions.append({"tool": "set_channel_scale", "arguments": {"channel": channel, "volts_per_div": float(scale)}, "reason": "模型建议调整垂直档位"})
    timebase = arguments.get("timebase_s_per_div")
    if timebase is not None:
        actions.append({"tool": "set_timebase_scale", "arguments": {"seconds_per_div": float(timebase)}, "reason": "模型建议调整观察时间窗口"})
    trigger = arguments.get("trigger_level_v")
    if trigger is not None:
        actions.append({"tool": "set_trigger_level", "arguments": {"level_v": float(trigger)}, "reason": "模型建议调整触发电平"})
    actions = _sanitize_actions(actions, settings)
    finish = bool(arguments.get("finish")) or round_number >= max_rounds
    if round_number >= max_rounds:
        actions = []
    requested_hypothesis = str(arguments.get("hypothesis") or "").strip()
    matched_hypothesis = next(
        (
            item
            for item in hypotheses
            if item["id"] == requested_hypothesis or item["label"] == requested_hypothesis
        ),
        hypotheses[0],
    )
    return {
        "hypothesis_id": matched_hypothesis["id"],
        "hypothesis": matched_hypothesis["label"],
        "assessment": str(arguments.get("assessment") or "模型未提供评估"),
        "confidence": max(0.0, min(float(arguments.get("confidence", 0.5)), 1.0)),
        "finish": finish,
        "actions": actions,
        "raw_actions": list(actions),
    }


def _sanitize_actions(actions: list[dict[str, Any]], settings: dict[str, float]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        tool = action.get("tool")
        if tool in seen or tool not in {"set_channel_scale", "set_timebase_scale", "set_trigger_level"}:
            continue
        arguments = dict(action.get("arguments") or {})
        if tool == "set_channel_scale":
            requested = float(arguments["volts_per_div"])
            minimum_visible = abs(settings.get("signal_median_v", settings["offset_v"]) - settings["offset_v"]) / 3.5
            arguments["volts_per_div"] = _nearest_ladder(max(requested, minimum_visible, 0.001), SCALE_LADDER)
            arguments["channel"] = int(settings["channel"])
            if math.isclose(arguments["volts_per_div"], settings["scale_v_per_div"], rel_tol=1e-6):
                continue
        elif tool == "set_timebase_scale":
            arguments["seconds_per_div"] = _nearest_ladder(float(arguments["seconds_per_div"]), TIMEBASE_LADDER)
            if math.isclose(arguments["seconds_per_div"], settings["timebase_s_per_div"], rel_tol=1e-6):
                continue
        else:
            arguments["level_v"] = round(float(arguments["level_v"]), 6)
            if math.isclose(arguments["level_v"], settings["trigger_level_v"], abs_tol=1e-6):
                continue
        sanitized.append({"tool": tool, "arguments": arguments, "reason": str(action.get("reason") or "闭环调整")})
        seen.add(tool)
    return sanitized


def _settings(status: dict[str, Any], channel: int) -> dict[str, Any]:
    channel_status = status["channels"][f"CH{channel}"]
    return {
        "channel": float(channel),
        "scale_v_per_div": float(channel_status["scale_v_per_div"]),
        "offset_v": float(channel_status.get("offset_v") or 0.0),
        "timebase_s_per_div": float(status["timebase"]["scale_s_per_div"]),
        "trigger_level_v": float(status["trigger"].get("level_v") or 0.0),
        "channel_enabled": bool(channel_status.get("enabled")),
        "trigger_mode": str(status["trigger"].get("mode") or ""),
        "trigger_source": str(status["trigger"].get("source") or ""),
    }


def _restored(current: dict[str, Any], original: dict[str, Any]) -> bool:
    return all(
        math.isclose(current[key], original[key], rel_tol=1e-6, abs_tol=1e-9)
        for key in ("scale_v_per_div", "offset_v", "timebase_s_per_div", "trigger_level_v")
    )


def _read_voltages(path: Path) -> list[float]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        values = [float(row["voltage_v"]) for row in csv.DictReader(stream)]
    if not values:
        raise ValueError("波形 CSV 没有采样点")
    return values


def _classify(
    frequency: float | None,
    vpp: float | None,
    rms: float | None,
    median: float,
    span: float,
    quantization: float,
    vertical_divisions: float | None,
    transitions: int,
    clipping_suspected: bool,
) -> str:
    if clipping_suspected:
        return "波形可能削顶，当前幅度无效"
    if quantization <= 0:
        return "未解析"
    resolution_bins = span / quantization
    resolved_variation = resolution_bins >= 5 and float(vertical_divisions or 0.0) >= 0.5
    resolved_level = abs(median) / quantization >= 5
    if frequency is not None and frequency > 0 and resolved_variation:
        return "周期信号"
    if span <= quantization * 2 and resolved_level and rms is not None:
        return "直流或缓慢变化信号"
    if not resolved_variation:
        return "低于有效分辨率的微小活动"
    noise_floor = max(quantization * 4, 0.03)
    if span <= noise_floor and (rms is not None and abs(rms) > noise_floor):
        return "直流或缓慢变化信号"
    if span > noise_floor and transitions <= 12:
        return "单次阶跃或接触变化"
    if vpp is not None and span <= max(vpp * 1.5, noise_floor * 2):
        return "非周期噪声或不稳定信号"
    return "未解析"


def _channel(text: str) -> int:
    match = re.search(r"(?:ch|通道)\s*([12])", text, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def _number(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _percentile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _nearest_ladder(value: float, ladder: tuple[float, ...]) -> float:
    return min(ladder, key=lambda item: abs(math.log10(max(value, 1e-15) / item)))


def _ceiling_ladder(value: float, ladder: tuple[float, ...]) -> float:
    return next((item for item in ladder if item >= value), ladder[-1])


def _usage(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    return {key: getattr(usage, key, None) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _decision_tool() -> dict[str, Any]:
    nullable_number = {"type": ["number", "null"]}
    return {
        "type": "function",
        "function": {
            "name": "submit_signal_decision",
            "description": "提交当前闭环轮次的信号判断和下一步安全调整。",
            "parameters": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "assessment": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "finish": {"type": "boolean"},
                    "vertical_scale_v_per_div": nullable_number,
                    "timebase_s_per_div": nullable_number,
                    "trigger_level_v": nullable_number,
                },
                "required": [
                    "hypothesis", "assessment", "confidence", "finish",
                    "vertical_scale_v_per_div", "timebase_s_per_div", "trigger_level_v",
                ],
            },
        },
    }
