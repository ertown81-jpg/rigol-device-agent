from __future__ import annotations

from collections.abc import Iterable
import math
from numbers import Real
from typing import Any, Callable

from .adapter import DeviceAdapter
from .models import RiskLevel, ToolSpec
from .policy import ExecutionPolicy


def _object_schema(properties: dict[str, Any], required: Iterable[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_device_status",
        "读取示波器身份、在线状态、通道、时基、采集和触发配置，不修改设备。",
        RiskLevel.READ_ONLY,
        _object_schema({}, ()),
    ),
    ToolSpec(
        "measure",
        "读取指定通道的自动测量值。可用项包括 FREQUENCY、PERIOD、VPP、VMAX、VMIN、VAVG、RMS、PDUTY、NDUTY、RISE_TIME、FALL_TIME。",
        RiskLevel.READ_ONLY,
        _object_schema(
            {
                "channel": {"type": "integer", "enum": [1, 2]},
                "measurements": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "FREQUENCY", "PERIOD", "VPP", "VMAX", "VMIN",
                            "VAVG", "RMS", "PDUTY", "NDUTY", "RISE_TIME", "FALL_TIME",
                        ],
                    },
                    "minItems": 1,
                },
            },
            ("channel", "measurements"),
        ),
    ),
    ToolSpec(
        "capture_waveform",
        "读取指定通道波形并保存 CSV 和元数据。NORMAL 适合快速查看；RAW 会临时停止采集并在完成后恢复。",
        RiskLevel.READ_ONLY,
        _object_schema(
            {
                "channel": {"type": "integer", "enum": [1, 2]},
                "mode": {"type": "string", "enum": ["NORMAL", "RAW"]},
                "max_points": {"type": ["integer", "null"], "minimum": 1, "maximum": 10_000_000},
            },
            ("channel", "mode", "max_points"),
        ),
    ),
    ToolSpec(
        "capture_screen",
        "读取示波器当前屏幕并保存 PNG，不修改显示配置。",
        RiskLevel.READ_ONLY,
        _object_schema({}, ()),
    ),
    ToolSpec(
        "set_channel_enabled",
        "开启或关闭指定通道。会改变设备状态。",
        RiskLevel.REVERSIBLE,
        _object_schema(
            {
                "channel": {"type": "integer", "enum": [1, 2]},
                "enabled": {"type": "boolean"},
            },
            ("channel", "enabled"),
        ),
    ),
    ToolSpec(
        "set_channel_scale",
        "设置指定通道的垂直档位，单位 V/div。会改变设备状态。",
        RiskLevel.REVERSIBLE,
        _object_schema(
            {
                "channel": {"type": "integer", "enum": [1, 2]},
                "volts_per_div": {"type": "number", "minimum": 0.001, "maximum": 10},
            },
            ("channel", "volts_per_div"),
        ),
    ),
    ToolSpec(
        "set_timebase_scale",
        "设置主时基，单位 s/div。会改变设备状态。",
        RiskLevel.REVERSIBLE,
        _object_schema(
            {"seconds_per_div": {"type": "number", "minimum": 5e-9, "maximum": 50}},
            ("seconds_per_div",),
        ),
    ),
    ToolSpec(
        "set_trigger_level",
        "设置边沿触发电平，单位 V。会改变设备状态。",
        RiskLevel.REVERSIBLE,
        _object_schema(
            {"level_v": {"type": "number", "minimum": -100, "maximum": 100}},
            ("level_v",),
        ),
    ),
    ToolSpec("run", "让示波器连续采集。会改变运行状态。", RiskLevel.REVERSIBLE, _object_schema({}, ())),
    ToolSpec("stop", "停止示波器采集。会改变运行状态。", RiskLevel.REVERSIBLE, _object_schema({}, ())),
    ToolSpec("single", "启动一次单次采集。会改变运行状态。", RiskLevel.GUARDED, _object_schema({}, ())),
)


class ToolRegistry:
    def __init__(
        self,
        adapter: DeviceAdapter,
        policy: ExecutionPolicy,
        specs: Iterable[ToolSpec] | None = None,
        result_validator: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.policy = policy
        selected = tuple(TOOL_SPECS if specs is None else specs)
        self._ordered_specs = selected
        self._specs = {spec.name: spec for spec in selected}
        self.result_validator = result_validator
        if len(self._specs) != len(selected):
            raise ValueError("工具注册表包含重复名称")

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        return self._ordered_specs

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"Agent 不具备工具 {name!r}") from exc

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        spec = self.spec(name)
        self.validate(name, arguments)
        self.policy.authorize(spec, arguments)
        result = self.adapter.invoke(name, arguments)
        if self.result_validator is not None:
            self.result_validator(name, result)
        return result

    def validate(self, name: str, arguments: dict[str, Any]) -> None:
        spec = self.spec(name)
        _validate_value(arguments, spec.parameters, path=name)

    def capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "risk": spec.risk.value,
                "parameters": spec.parameters,
            }
            for spec in self._ordered_specs
        ]

    def openai_tools(self) -> list[dict[str, Any]]:
        return [spec.openai_schema() for spec in self._ordered_specs]


def _validate_value(value: Any, schema: dict[str, Any], *, path: str) -> None:
    allowed_types = schema.get("type")
    if isinstance(allowed_types, str):
        allowed_types = [allowed_types]
    if allowed_types and not any(_matches_type(value, item) for item in allowed_types):
        raise ValueError(f"{path} 类型无效，期望 {allowed_types}")

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} 不在允许值中：{value!r}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise ValueError(f"{path} 缺少参数：{required}")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(f"{path} 包含未知参数：{', '.join(sorted(extras))}")
        for key, child in value.items():
            if key in properties:
                _validate_value(child, properties[key], path=f"{path}.{key}")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path} 项目数量不足")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_value(item, item_schema, path=f"{path}[{index}]")

    if isinstance(value, Real) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise ValueError(f"{path} 必须是有限数值")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} 小于最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} 大于最大值 {schema['maximum']}")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, Real) and not isinstance(value, bool)
    return False
