from __future__ import annotations

from pathlib import Path

from ..adapter import RigolDeviceAdapter, SimulatedRigolAdapter
from ..planner import RuleBasedPlanner
from ..policy import validate_arguments
from ..tools import TOOL_SPECS
from .base import AdaptiveProfile, DevicePack


PLANNER_INSTRUCTIONS = """
你是 RIGOL DS1102Z-E 设备任务规划器。你的唯一职责是把用户请求转换为受控工具计划。
只能使用给定能力列表中的工具，禁止生成、建议或请求执行原始 SCPI。
用户没有明确要求修改设备时，只能选择只读工具。
执行测量、波形或截图前，应先加入 get_device_status。
计划必须只包含完成目标所必需的步骤。超出能力边界时提交空 steps，不得用近似工具冒充完成。
不要虚构设备状态、测量结果或文件路径。
""".strip()


def _adapter(config_path: str | Path) -> RigolDeviceAdapter:
    return RigolDeviceAdapter(config_path)


def _simulator(scenario: str, output_dir: str | Path) -> SimulatedRigolAdapter:
    selected = "sine" if scenario == "default" else scenario
    return SimulatedRigolAdapter(selected, output_dir)


RIGOL_DS1102ZE_PACK = DevicePack(
    pack_id="rigol_ds1102ze",
    display_name="RIGOL DS1102Z-E 示波器",
    description="已完成 USBTMC/VISA 实机验证的双通道数字示波器能力包。",
    device_class="oscilloscope",
    manufacturers=("RIGOL TECHNOLOGIES",),
    model_patterns=(r"DS1102Z-E", r"DS1\d{3}Z-E"),
    transports=("USBTMC/VISA", "LAN/VISA（待实机验证）"),
    tool_specs=TOOL_SPECS,
    adapter_factory=_adapter,
    simulator_factory=_simulator,
    planner_instructions=PLANNER_INSTRUCTIONS,
    rule_planner_factory=RuleBasedPlanner,
    argument_validator=validate_arguments,
    adaptive=AdaptiveProfile(
        kind="oscilloscope_signal",
        change_tools=("set_channel_scale", "set_timebase_scale", "set_trigger_level"),
        max_rounds=4,
    ),
    documentation=(
        "DS1000ZE_ProgrammingGuide_EN.pdf",
        "DS1000Z-E_UserGuide_CN.pdf",
        "docs/CAPABILITIES.md",
        "docs/VALIDATION.md",
    ),
    example_tasks=(
        "读取 CH1 的频率、峰峰值和有效值",
        "保存 CH1 波形和截图",
        "读取设备状态和配置",
        "分析 CH1 当前信号，把它测清楚",
    ),
)
