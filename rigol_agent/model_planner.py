from __future__ import annotations

import json
import os
import time
from typing import Any

from .models import PlanStep, TaskPlan
from .planner import RuleBasedPlanner
from .tools import ToolRegistry


PROVIDERS: dict[str, dict[str, str | None]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "doubao": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-seed-2-0-lite-260215",
        "key_env": "ARK_API_KEY",
    },
    "openai": {
        "base_url": None,
        "default_model": "gpt-5.6-terra",
        "key_env": "OPENAI_API_KEY",
    },
}


SYSTEM_INSTRUCTIONS = """
你是 RIGOL DS1102Z-E 设备任务规划器。你的唯一职责是把用户请求转换为受控工具计划。
只能使用给定能力列表中的工具，禁止生成、建议或请求执行原始 SCPI。
用户没有明确要求修改设备时，只能选择只读工具。
执行测量、波形或截图前，应先加入 get_device_status。
计划必须只包含完成目标所必需的步骤。超出能力边界时提交空 steps，不得用近似工具冒充完成。
不要虚构设备状态、测量结果或文件路径。
""".strip()


class CompatibleModelPlanner:
    """Plan through an OpenAI-compatible Chat Completions endpoint."""

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        provider: str,
        model: str | None = None,
        api_key: str | None = None,
        client: Any | None = None,
        system_instructions: str | None = None,
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError(f"不支持的模型供应商：{provider}")
        settings = PROVIDERS[provider]
        key = api_key or os.environ.get("RIGOL_MODEL_API_KEY") or os.environ.get(str(settings["key_env"]))
        if client is None and not key:
            raise RuntimeError(f"未设置 {settings['key_env']} 或 RIGOL_MODEL_API_KEY")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("缺少模型依赖，请安装 requirements-agent.txt") from exc
            kwargs: dict[str, Any] = {"api_key": key}
            if settings["base_url"]:
                kwargs["base_url"] = settings["base_url"]
            client = OpenAI(**kwargs)
        self.client = client
        self.tools = tools
        self.provider = provider
        self.model = model or os.environ.get("RIGOL_MODEL_NAME") or str(settings["default_model"])
        self.name = f"{provider}:{self.model}"
        self.system_instructions = system_instructions or SYSTEM_INSTRUCTIONS

    def plan(self, request: str) -> TaskPlan:
        capability_text = json.dumps(self.tools.capabilities(), ensure_ascii=False, separators=(",", ":"))
        planning_started = time.perf_counter()
        request_options: dict[str, Any] = {}
        if self.provider == "deepseek":
            request_options["extra_body"] = {"thinking": {"type": "disabled"}}
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": f"{self.system_instructions}\n\n受控能力列表：\n{capability_text}",
                },
                {"role": "user", "content": request},
            ],
            tools=[_submit_plan_tool()],
            tool_choice={"type": "function", "function": {"name": "submit_device_plan"}},
            temperature=0,
            **request_options,
        )
        message = response.choices[0].message
        planning_duration_ms = round((time.perf_counter() - planning_started) * 1000, 1)
        calls = getattr(message, "tool_calls", None) or []
        if not calls:
            raise ValueError("模型没有提交设备计划")
        arguments = json.loads(calls[0].function.arguments)
        raw_steps = arguments.get("steps", [])
        if not isinstance(raw_steps, list):
            raise ValueError("模型计划的 steps 必须是数组")
        steps: list[PlanStep] = []
        for item in raw_steps:
            if not isinstance(item, dict):
                raise ValueError("模型计划步骤必须是对象")
            tool = str(item.get("tool", ""))
            tool_arguments = item.get("arguments", {})
            if not isinstance(tool_arguments, dict):
                raise ValueError(f"工具 {tool} 的 arguments 必须是对象")
            self.tools.validate(tool, tool_arguments)
            steps.append(
                PlanStep(
                    tool=tool,
                    arguments=tool_arguments,
                    reason=str(item.get("reason", "由模型根据用户目标选择")),
                )
            )
        return TaskPlan(
            request=request,
            steps=steps,
            summary=f"模型规划了 {len(steps)} 个受控工具步骤" if steps else "请求超出设备能力边界，未生成设备步骤",
            planner=self.name,
            planning={
                "provider": self.provider,
                "model": self.model,
                "duration_ms": planning_duration_ms,
                "response_id": getattr(response, "id", None),
                "usage": _usage_dict(getattr(response, "usage", None)),
            },
        )


class FallbackPlanner:
    """Use deterministic rules if a remote planner is unavailable or invalid."""

    def __init__(self, primary: CompatibleModelPlanner, fallback: Any | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or RuleBasedPlanner()
        self.provider = primary.provider
        self.model = primary.model
        self.name = primary.name
        self.last_error: str | None = None

    def plan(self, request: str) -> TaskPlan:
        try:
            plan = self.primary.plan(request)
            self.last_error = None
            return plan
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            plan = self.fallback.plan(request)
            plan.planner = f"{self.name}->rules"
            plan.summary = f"模型规划不可用，已使用本地规则；{plan.summary}"
            plan.planning = {"provider": self.provider, "model": self.model, "fallback_error": self.last_error}
            return plan

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "fallback": "rules",
            "last_error": self.last_error,
        }


def build_planner(
    name: str,
    tools: ToolRegistry,
    model: str | None = None,
    *,
    system_instructions: str | None = None,
    fallback: Any | None = None,
) -> RuleBasedPlanner | CompatibleModelPlanner | FallbackPlanner:
    selected = name.lower()
    automatic = selected == "auto"
    if automatic:
        selected = os.environ.get("RIGOL_MODEL_PROVIDER", "rules").lower()
        if selected not in PROVIDERS or not _has_key(selected):
            selected = "rules"
    if selected == "rules":
        return fallback or RuleBasedPlanner()
    primary = CompatibleModelPlanner(
        tools,
        provider=selected,
        model=model,
        system_instructions=system_instructions,
    )
    return FallbackPlanner(primary, fallback=fallback) if automatic else primary


def planner_status(planner: Any) -> dict[str, Any]:
    if hasattr(planner, "status"):
        return planner.status()
    return {"provider": "rules", "model": None, "fallback": None, "last_error": None}


def _has_key(provider: str) -> bool:
    settings = PROVIDERS[provider]
    return bool(os.environ.get("RIGOL_MODEL_API_KEY") or os.environ.get(str(settings["key_env"])))


def _submit_plan_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "submit_device_plan",
            "description": "提交完整的受控设备工具执行计划。此函数只规划，不直接执行设备命令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {"type": "string"},
                                "arguments": {"type": "object"},
                                "reason": {"type": "string"},
                            },
                            "required": ["tool", "arguments", "reason"],
                        },
                    }
                },
                "required": ["steps"],
            },
        },
    }


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return {key: getattr(usage, key, None) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
