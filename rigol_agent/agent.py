from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Protocol

from src.utils import now_iso, save_json

from .models import TaskPlan, TaskResult, ToolResult
from .diagnostics import analyze_results
from .reporting import save_html_report
from .tools import ToolRegistry


class Planner(Protocol):
    def plan(self, request: str) -> TaskPlan: ...


class RigolAgent:
    def __init__(self, planner: Planner, tools: ToolRegistry, output_dir: str | Path = "output/agent/sessions") -> None:
        self.planner = planner
        self.tools = tools
        self.output_dir = Path(output_dir)
        self.adaptive_runner = None

    def plan(self, request: str) -> TaskPlan:
        return self.planner.plan(request)

    def run(self, request: str) -> TaskResult:
        if self.adaptive_runner is not None and self.adaptive_runner.accepts(request):
            return self.adaptive_runner.run(request)
        plan = self.plan(request)
        started_at = now_iso()
        session_id = f"{started_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        results: list[ToolResult] = []

        for step in plan.steps:
            step_started = now_iso()
            spec = self.tools.spec(step.tool)
            try:
                data = self.tools.execute(step.tool, step.arguments)
                result = ToolResult(
                    tool=step.tool,
                    arguments=step.arguments,
                    success=True,
                    started_at=step_started,
                    finished_at=now_iso(),
                    data=data,
                    risk=spec.risk,
                )
            except Exception as exc:
                result = ToolResult(
                    tool=step.tool,
                    arguments=step.arguments,
                    success=False,
                    started_at=step_started,
                    finished_at=now_iso(),
                    error=f"{type(exc).__name__}: {exc}",
                    risk=spec.risk,
                )
            results.append(result)
            self._append_audit(session_id, result)
            if not result.success:
                break

        success = bool(plan.steps) and len(results) == len(plan.steps) and all(item.success for item in results)
        summary = self._summarize(plan, results, success)
        task_result = TaskResult(
            session_id=session_id,
            request=request,
            success=success,
            plan=plan,
            results=results,
            started_at=started_at,
            finished_at=now_iso(),
            summary=summary,
            analysis=analyze_results(results),
        )
        output_path = self.output_dir / f"{session_id}.json"
        report_path = self.output_dir / f"{session_id}.html"
        task_result.output_path = str(output_path)
        task_result.report_path = str(report_path)
        save_json(output_path, task_result.to_dict())
        save_html_report(task_result, report_path)
        return task_result

    def _append_audit(self, session_id: str, result: ToolResult) -> None:
        path = self.output_dir.parent / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {"session_id": session_id, **result.to_dict()}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    @staticmethod
    def _summarize(plan: TaskPlan, results: list[ToolResult], success: bool) -> str:
        if not plan.steps:
            return "无法将请求映射到已开放的设备工具，未执行任何命令。"
        if not success:
            failure = next((item for item in results if not item.success), None)
            return f"任务在 {failure.tool if failure else '未知步骤'} 处停止：{failure.error if failure else '未知错误'}"
        files: list[str] = []
        for item in results:
            if isinstance(item.data, dict):
                for key in ("csv_path", "metadata_path", "plot_path", "output"):
                    if item.data.get(key):
                        files.append(str(item.data[key]))
        suffix = f"；生成文件：{', '.join(files)}" if files else ""
        return f"任务成功完成，共执行 {len(results)} 个工具步骤{suffix}。"


# Generic name for new integrations; keep RigolAgent for backward compatibility.
DeviceAgent = RigolAgent
