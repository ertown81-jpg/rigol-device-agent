from __future__ import annotations

import argparse
import time

from common import add_connection_arguments, public_identity, resolve_connection
from src.connection import discover_resources
from src.rigol_scope import RigolDS1102ZE
from src.utils import now_iso, setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="轮询监听示波器上线、离线和重新连接")
    add_connection_arguments(parser)
    parser.add_argument("--interval", type=float, help="轮询间隔，秒")
    args = parser.parse_args()
    options = resolve_connection(args, require_resource=False)
    interval = args.interval or options["monitor_interval_s"]
    if interval < 0.5:
        raise SystemExit("轮询间隔不得小于 0.5 秒")

    logger = setup_logging()
    scope: RigolDS1102ZE | None = None
    current_resource = options["resource"] or ""
    print(f"{now_iso()} 监听启动，间隔 {interval:g} 秒；按 Ctrl+C 结束")

    try:
        while True:
            if scope is not None:
                try:
                    scope.identify()
                except Exception as exc:
                    print(f"{now_iso()} OFFLINE {scope.resource} {type(exc).__name__}: {exc}")
                    scope.close()
                    scope = None
            else:
                candidates: list[str]
                if current_resource:
                    candidates = [current_resource]
                else:
                    try:
                        candidates = discover_resources(options["backend"])
                    except Exception as exc:
                        print(f"{now_iso()} SCAN_ERROR {type(exc).__name__}: {exc}")
                        candidates = []
                for candidate in candidates:
                    probe = RigolDS1102ZE(
                        candidate,
                        backend=options["backend"],
                        timeout_ms=options["timeout_ms"],
                        query_delay_s=options["query_delay_s"],
                        logger=logger,
                    )
                    try:
                        probe.connect()
                        identity = probe.identify()
                    except Exception:
                        probe.close()
                        continue
                    if "RIGOL" not in identity.get("manufacturer", "").upper():
                        probe.close()
                        continue
                    scope = probe
                    current_resource = candidate
                    print(
                        f"{now_iso()} ONLINE {candidate} "
                        f"{public_identity(identity, args.show_serial)['raw']}"
                    )
                    break
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n{now_iso()} 监听结束")
    finally:
        if scope is not None:
            scope.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
