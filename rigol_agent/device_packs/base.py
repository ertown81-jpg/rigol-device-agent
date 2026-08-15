from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Callable, Protocol

from ..models import RiskLevel, ToolSpec


class DeviceAdapter(Protocol):
    """The only transport-facing interface used by the Agent core."""

    def invoke(self, tool: str, arguments: dict[str, Any]) -> Any: ...

    def close(self) -> None: ...


class Planner(Protocol):
    def plan(self, request: str) -> Any: ...


ArgumentValidator = Callable[[str, dict[str, Any]], None]
ResultValidator = Callable[[str, Any], None]
AdapterFactory = Callable[[str | Path], DeviceAdapter]
SimulatorFactory = Callable[[str, str | Path], DeviceAdapter]
PlannerFactory = Callable[[], Planner]


def validate_standard_result(tool: str, result: Any) -> None:
    """Validate the cross-device result fields consumed by the core and web UI."""

    if not isinstance(result, dict):
        raise ValueError(f"工具 {tool} 必须返回 JSON 对象")
    if tool != "get_device_status":
        return
    if not isinstance(result.get("online"), bool):
        raise ValueError("get_device_status.online 必须是布尔值")
    identity = result.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("get_device_status.identity 必须是对象")
    for key in ("manufacturer", "model", "serial", "firmware"):
        if not isinstance(identity.get(key), str):
            raise ValueError(f"get_device_status.identity.{key} 必须是字符串")
    if not isinstance(result.get("status"), dict):
        raise ValueError("get_device_status.status 必须是对象")
    if not isinstance(result.get("errors"), list):
        raise ValueError("get_device_status.errors 必须是数组")


@dataclass(frozen=True)
class AdaptiveProfile:
    """Optional closed-loop controller supplied by a device pack."""

    kind: str
    change_tools: tuple[str, ...]
    max_rounds: int = 4


@dataclass(frozen=True)
class DevicePack:
    """Trusted, reviewable contract between one device family and the Agent core."""

    pack_id: str
    display_name: str
    description: str
    device_class: str
    manufacturers: tuple[str, ...]
    model_patterns: tuple[str, ...]
    transports: tuple[str, ...]
    tool_specs: tuple[ToolSpec, ...]
    adapter_factory: AdapterFactory
    simulator_factory: SimulatorFactory | None
    planner_instructions: str
    rule_planner_factory: PlannerFactory
    argument_validator: ArgumentValidator
    adaptive: AdaptiveProfile | None = None
    documentation: tuple[str, ...] = field(default_factory=tuple)
    example_tasks: tuple[str, ...] = field(default_factory=tuple)
    schema_version: int = 1
    result_validator: ResultValidator = validate_standard_result

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.pack_id):
            raise ValueError(f"设备包 ID 无效: {self.pack_id!r}")
        if self.schema_version != 1:
            raise ValueError(f"不支持的设备包 schema_version: {self.schema_version}")
        if not self.display_name.strip() or not self.description.strip():
            raise ValueError("设备包必须提供名称和说明")
        if not self.model_patterns:
            raise ValueError("设备包必须声明至少一个型号匹配规则")
        for pattern in self.model_patterns:
            re.compile(pattern)
        names = [spec.name for spec in self.tool_specs]
        if len(names) != len(set(names)):
            raise ValueError(f"设备包 {self.pack_id} 存在重复工具名")
        if "get_device_status" not in names:
            raise ValueError("设备包必须实现标准只读工具 get_device_status")
        status_spec = next(spec for spec in self.tool_specs if spec.name == "get_device_status")
        if status_spec.risk is not RiskLevel.READ_ONLY:
            raise ValueError("get_device_status 必须是 read_only")
        if any(spec.risk is RiskLevel.PROHIBITED for spec in self.tool_specs):
            raise ValueError("禁止能力不能注册为模型可见工具")
        if self.adaptive is not None:
            missing = set(self.adaptive.change_tools) - set(names)
            if missing:
                raise ValueError(f"自适应控制引用了未注册工具: {', '.join(sorted(missing))}")
            if not 1 <= self.adaptive.max_rounds <= 8:
                raise ValueError("自适应最大轮数必须位于 1 到 8")

    def create_adapter(
        self,
        config_path: str | Path,
        *,
        simulate: bool = False,
        scenario: str = "default",
        output_dir: str | Path = "output/agent/simulated",
    ) -> DeviceAdapter:
        if simulate:
            if self.simulator_factory is None:
                raise ValueError(f"设备包 {self.pack_id} 没有提供模拟器")
            return self.simulator_factory(scenario, output_dir)
        return self.adapter_factory(config_path)

    def metadata(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "display_name": self.display_name,
            "description": self.description,
            "device_class": self.device_class,
            "manufacturers": list(self.manufacturers),
            "model_patterns": list(self.model_patterns),
            "transports": list(self.transports),
            "tool_count": len(self.tool_specs),
            "adaptive": None
            if self.adaptive is None
            else {
                "kind": self.adaptive.kind,
                "change_tools": list(self.adaptive.change_tools),
                "max_rounds": self.adaptive.max_rounds,
            },
            "documentation": list(self.documentation),
            "example_tasks": list(self.example_tasks),
        }


class DevicePackRegistry:
    """Explicit allow-list. Importing an arbitrary directory never grants hardware access."""

    def __init__(self) -> None:
        self._packs: dict[str, DevicePack] = {}

    def register(self, pack: DevicePack) -> None:
        pack.validate()
        if pack.pack_id in self._packs:
            raise ValueError(f"设备包已注册: {pack.pack_id}")
        self._packs[pack.pack_id] = pack

    def get(self, pack_id: str) -> DevicePack:
        try:
            return self._packs[pack_id]
        except KeyError as exc:
            available = ", ".join(sorted(self._packs)) or "无"
            raise KeyError(f"未知设备包 {pack_id!r}；可用设备包: {available}") from exc

    def list(self) -> list[DevicePack]:
        return [self._packs[key] for key in sorted(self._packs)]
