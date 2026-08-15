from __future__ import annotations

import argparse
import time

from common import add_connection_arguments, print_json, public_identity, resolve_connection
from src.connection import discover_resources
from src.rigol_scope import RigolDS1102ZE
from src.utils import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="枚举 VISA 资源并尝试读取 *IDN?")
    add_connection_arguments(parser)
    args = parser.parse_args()
    options = resolve_connection(args, require_resource=False)
    extras = [options["resource"]] if options["resource"] else []

    started = time.perf_counter()
    all_resources = discover_resources(options["backend"], extras)
    resources = [
        resource
        for resource in all_resources
        if resource in extras
        or ("USB" in resource.upper() and "0X1AB1" in resource.upper())
        or resource.upper().startswith("TCPIP")
    ]
    print(
        f"发现 {len(all_resources)} 个 VISA 资源，其中 {len(resources)} 个需要探测"
        f"（{(time.perf_counter() - started) * 1000:.1f} ms）"
    )
    if not resources:
        print("未发现资源。USB 请检查驱动和 USB Device=Computer；LAN 可用 --ip 直接探测。")
        return 1

    results = []
    logger = setup_logging()
    for resource in resources:
        item = {"resource": resource, "connected": False}
        scope = RigolDS1102ZE(
            resource,
            backend=options["backend"],
            timeout_ms=options["timeout_ms"],
            query_delay_s=options["query_delay_s"],
            logger=logger,
        )
        probe_started = time.perf_counter()
        try:
            with scope:
                identity = scope.identify()
            item.update(
                {
                    "connected": True,
                    "elapsed_ms": round((time.perf_counter() - probe_started) * 1000, 1),
                    "identity": public_identity(identity, args.show_serial),
                }
            )
        except Exception as exc:
            item.update(
                {
                    "elapsed_ms": round((time.perf_counter() - probe_started) * 1000, 1),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        results.append(item)
    print_json(results)
    return 0 if any(item["connected"] for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
