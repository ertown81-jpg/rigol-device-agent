from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connection import make_lan_resource
from src.rigol_scope import RigolDS1102ZE
from src.utils import load_config, redact_serial, setup_logging


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config.json", help="配置文件，默认 config.json")
    parser.add_argument("--resource", help="VISA 资源地址；优先级高于配置文件")
    parser.add_argument("--ip", help="示波器 LAN IP；自动转换为 TCPIP0::<IP>::INSTR")
    parser.add_argument(
        "--backend",
        help="PyVISA 后端，例如 @py；留空时使用系统 VISA（Windows USB 推荐）",
    )
    parser.add_argument("--timeout-ms", type=int, help="通信超时，毫秒")
    parser.add_argument("--show-serial", action="store_true", help="显示完整序列号")


def resolve_connection(args: argparse.Namespace, *, require_resource: bool = True) -> dict[str, Any]:
    config = load_config(args.config)
    resource = args.resource or config.get("resource") or ""
    ip = args.ip or config.get("ip") or ""
    if not resource and ip:
        resource = make_lan_resource(str(ip))
    if require_resource and not resource:
        raise SystemExit(
            "未指定设备。请复制 config.example.json 为 config.json 并填写 resource/ip，"
            "或使用 --resource/--ip。"
        )
    backend = args.backend if args.backend is not None else config.get("backend")
    return {
        "resource": resource,
        "backend": backend or None,
        "timeout_ms": args.timeout_ms or int(config.get("timeout_ms", 10_000)),
        "query_delay_s": float(config.get("query_delay_s", 0.05)),
        "monitor_interval_s": float(config.get("monitor_interval_s", 3.0)),
    }


def build_scope(args: argparse.Namespace) -> RigolDS1102ZE:
    options = resolve_connection(args)
    logger = setup_logging()
    return RigolDS1102ZE(
        options["resource"],
        backend=options["backend"],
        timeout_ms=options["timeout_ms"],
        query_delay_s=options["query_delay_s"],
        logger=logger,
    )


def public_identity(identity: dict[str, Any], show_serial: bool = False) -> dict[str, Any]:
    result = dict(identity)
    if not show_serial:
        result["serial"] = redact_serial(
            f"{identity.get('manufacturer', '')},{identity.get('model', '')},"
            f"{identity.get('serial', '')},{identity.get('firmware', '')}"
        ).split(",")[2]
        result["raw"] = redact_serial(str(identity.get("raw", "")))
    return result


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
