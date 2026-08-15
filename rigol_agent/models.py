from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE = "reversible"
    GUARDED = "guarded"
    PROHIBITED = "prohibited"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risk: RiskLevel
    parameters: dict[str, Any]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "strict": True,
            "parameters": self.parameters,
        }


@dataclass(frozen=True)
class PlanStep:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class TaskPlan:
    request: str
    steps: list[PlanStep]
    summary: str
    planner: str = "rules"
    planning: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "summary": self.summary,
            "planner": self.planner,
            "planning": self.planning,
            "steps": [asdict(step) for step in self.steps],
        }


@dataclass
class ToolResult:
    tool: str
    arguments: dict[str, Any]
    success: bool
    started_at: str
    finished_at: str
    data: Any = None
    error: str | None = None
    risk: RiskLevel = RiskLevel.READ_ONLY

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["risk"] = self.risk.value
        return result


@dataclass
class TaskResult:
    session_id: str
    request: str
    success: bool
    plan: TaskPlan
    results: list[ToolResult]
    started_at: str
    finished_at: str
    summary: str
    analysis: dict[str, Any] = field(default_factory=dict)
    output_path: str | None = None
    report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "request": self.request,
            "success": self.success,
            "plan": self.plan.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary,
            "analysis": self.analysis,
            "output_path": self.output_path,
            "report_path": self.report_path,
        }
