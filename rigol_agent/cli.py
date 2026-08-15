from __future__ import annotations

import argparse
import json
from typing import Any

from .adaptive import ClosedLoopSignalAgent
from .agent import RigolAgent
from .device_packs import DevicePack, get_device_pack, list_device_packs
from .monitor import monitor_events
from .model_planner import build_planner
from .policy import ExecutionPolicy
from .service import serve
from .tools import ToolRegistry


def _print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _adapter(args: argparse.Namespace, pack: DevicePack) -> Any:
    return pack.create_adapter(
        args.config,
        simulate=args.simulate,
        scenario=args.simulate_scenario,
    )


def _agent(args: argparse.Namespace, adapter: Any, pack: DevicePack) -> tuple[RigolAgent, ToolRegistry]:
    policy = ExecutionPolicy(
        allow_changes=getattr(args, "allow_changes", False),
        allow_guarded=getattr(args, "allow_guarded", False),
        device_label=pack.display_name,
        argument_validator=pack.argument_validator,
    )
    tools = ToolRegistry(adapter, policy, pack.tool_specs, pack.result_validator)
    fallback = pack.rule_planner_factory()
    planner = build_planner(
        getattr(args, "planner", "rules"),
        tools,
        model=getattr(args, "model", None),
        system_instructions=pack.planner_instructions,
        fallback=fallback,
    )
    agent = RigolAgent(planner, tools)
    if pack.adaptive is not None and pack.adaptive.kind == "oscilloscope_signal":
        agent.adaptive_runner = ClosedLoopSignalAgent(
            tools,
            planner,
            max_rounds=pack.adaptive.max_rounds,
            allowed_change_tools=pack.adaptive.change_tools,
            allow_adaptive_changes=getattr(args, "allow_adaptive_changes", False),
        )
    return agent, tools


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="可扩展智能设备 Agent")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--device", default="rigol_ds1102ze", help="选择已注册设备能力包")
    parser.add_argument("--simulate", action="store_true", help="使用所选设备包的模拟器")
    parser.add_argument("--simulate-scenario", default="default", help="选择设备包提供的模拟场景")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("devices", help="列出已注册设备能力包")

    subparsers.add_parser("capabilities", help="列出 Agent 能力和风险级别")

    plan = subparsers.add_parser("plan", help="只生成计划，不连接设备")
    plan.add_argument("request")
    plan.add_argument("--planner", choices=("rules", "auto", "deepseek", "doubao", "openai"), default="rules")
    plan.add_argument("--model")

    run = subparsers.add_parser("run", help="执行自然语言任务")
    run.add_argument("request")
    run.add_argument("--planner", choices=("rules", "auto", "deepseek", "doubao", "openai"), default="rules")
    run.add_argument("--model")
    run.add_argument("--allow-changes", action="store_true", help="允许可恢复的设备修改")
    run.add_argument("--allow-adaptive-changes", action="store_true", help="为闭环实验授予限定通道、限定工具和限定次数的临时修改租约")
    run.add_argument("--allow-guarded", action="store_true", help="允许单次采集等受保护动作")

    monitor = subparsers.add_parser("monitor", help="监听上线、离线和通道变化")
    monitor.add_argument("--interval", type=float, default=3.0)
    monitor.add_argument("--count", type=int)

    api = subparsers.add_parser("serve", help="启动本机 HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8765)
    api.add_argument("--allow-changes", action="store_true")
    api.add_argument("--allow-adaptive-changes", action="store_true")
    api.add_argument("--planner", choices=("rules", "auto", "deepseek", "doubao", "openai"), default="auto")
    api.add_argument("--model")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "devices":
        _print({"device_packs": [pack.metadata() for pack in list_device_packs()]})
        return 0
    pack = get_device_pack(args.device)
    adapter = _adapter(args, pack)
    try:
        agent, tools = _agent(args, adapter, pack)
        if args.command == "capabilities":
            _print({"device_pack": pack.metadata(), "tools": tools.capabilities()})
            return 0
        if args.command == "plan":
            _print(agent.plan(args.request).to_dict())
            return 0
        if args.command == "run":
            result = agent.run(args.request)
            _print(result.to_dict())
            return 0 if result.success else 2
        if args.command == "monitor":
            for event in monitor_events(adapter, interval_s=args.interval, count=args.count):
                _print(event)
            return 0
        if args.command == "serve":
            serve(
                adapter,
                host=args.host,
                port=args.port,
                allow_changes=args.allow_changes,
                allow_adaptive_changes=args.allow_adaptive_changes,
                planner_name=args.planner,
                model=args.model,
                device_pack=pack,
                adapter_factory=lambda selected: _adapter(args, selected),
            )
            return 0
        raise AssertionError(f"未处理命令: {args.command}")
    finally:
        if args.command != "serve":
            adapter.close()


if __name__ == "__main__":
    raise SystemExit(main())
