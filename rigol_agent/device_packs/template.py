"""Copy this file when starting a new device pack; it is intentionally not registered."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import PlanStep, RiskLevel, TaskPlan, ToolSpec
from .base import DevicePack


STATUS_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


class ExampleAdapter:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path)

    def invoke(self, tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "get_device_status":
            return {
                "online": True,
                "identity": {"manufacturer": "EXAMPLE", "model": "MODEL", "serial": "EXA***001", "firmware": "UNKNOWN"},
                "status": {},
                "errors": [],
            }
        raise KeyError(f"未实现工具: {tool}")

    def close(self) -> None:
        return None


class ExampleRulePlanner:
    def plan(self, request: str) -> TaskPlan:
        return TaskPlan(
            request=request,
            steps=[PlanStep("get_device_status", {}, "读取设备身份和状态")],
            summary="模板只实现设备状态读取",
        )


def validate_example_arguments(tool: str, arguments: dict[str, Any]) -> None:
    if tool != "get_device_status":
        raise ValueError(f"模板不允许工具: {tool}")


EXAMPLE_PACK = DevicePack(
    pack_id="vendor_model",
    display_name="厂商 型号",
    description="新增设备能力包模板；完成实现和评审前不要注册。",
    device_class="replace_me",
    manufacturers=("VENDOR",),
    model_patterns=(r"MODEL",),
    transports=("USB/Serial/HTTP",),
    tool_specs=(ToolSpec("get_device_status", "读取设备身份和状态。", RiskLevel.READ_ONLY, STATUS_SCHEMA),),
    adapter_factory=ExampleAdapter,
    simulator_factory=None,
    planner_instructions="只能使用给定工具；禁止生成底层协议命令；证据不足时返回空计划。",
    rule_planner_factory=ExampleRulePlanner,
    argument_validator=validate_example_arguments,
    documentation=("填写编程手册、用户手册、协议或 SDK 文档路径",),
    example_tasks=("读取设备状态",),
)
