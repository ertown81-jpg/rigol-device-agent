from __future__ import annotations

import argparse
from pathlib import Path

from common import add_connection_arguments, build_scope, print_json, public_identity
from src.utils import timestamp_for_filename


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 :DISP:DATA? 直接读取屏幕 PNG")
    add_connection_arguments(parser)
    parser.add_argument("--output", help="PNG 输出路径")
    args = parser.parse_args()
    output = Path(args.output or f"output/screen_{timestamp_for_filename()}.png")
    scope = build_scope(args)

    try:
        with scope:
            identity = public_identity(scope.identify(), args.show_serial)
            saved = scope.capture_screen(output)
            errors = scope.get_errors()
        print_json(
            {
                "success": True,
                "resource": scope.resource,
                "identity": identity,
                "output": str(saved),
                "bytes": saved.stat().st_size,
                "errors": errors,
            }
        )
        return 0
    except Exception as exc:
        print_json(
            {
                "success": False,
                "resource": scope.resource,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
