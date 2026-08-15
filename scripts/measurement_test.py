from __future__ import annotations

import argparse

from common import add_connection_arguments, build_scope, print_json, public_identity


DEFAULT_MEASUREMENTS = [
    "FREQ",
    "PERIOD",
    "VMAX",
    "VMIN",
    "VPP",
    "VAVG",
    "VRMS",
    "PDUTY",
    "RTIME",
    "FTIME",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="查询常用自动测量值；无效值显示为 null")
    add_connection_arguments(parser)
    parser.add_argument("--channel", type=int, choices=(1, 2), default=1)
    parser.add_argument(
        "--measurements",
        nargs="+",
        default=DEFAULT_MEASUREMENTS,
        help="测量项列表",
    )
    args = parser.parse_args()

    scope = build_scope(args)
    try:
        with scope:
            identity = public_identity(scope.identify(), args.show_serial)
            values = {
                name: scope.measure(args.channel, name) for name in args.measurements
            }
            errors = scope.get_errors()
        print_json(
            {
                "success": True,
                "resource": scope.resource,
                "identity": identity,
                "channel": args.channel,
                "measurements": values,
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
