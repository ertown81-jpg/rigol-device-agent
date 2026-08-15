from __future__ import annotations

import argparse
import time

from common import add_connection_arguments, build_scope, print_json, public_identity


def main() -> int:
    parser = argparse.ArgumentParser(description="连接示波器并读取身份、状态和错误队列")
    add_connection_arguments(parser)
    args = parser.parse_args()

    started = time.perf_counter()
    scope = build_scope(args)
    try:
        with scope:
            identity = public_identity(scope.identify(), args.show_serial)
            status = scope.get_status()
            errors = scope.get_errors()
        print_json(
            {
                "connected": True,
                "resource": scope.resource,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "identity": identity,
                "status": status,
                "errors": errors,
            }
        )
        return 0
    except Exception as exc:
        print_json(
            {
                "connected": False,
                "resource": scope.resource,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
