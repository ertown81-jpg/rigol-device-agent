from __future__ import annotations

import argparse
import time

from common import add_connection_arguments, build_scope, print_json, public_identity


def main() -> int:
    parser = argparse.ArgumentParser(description="安全测试 STOP/RUN，并可选测试通道开关")
    add_connection_arguments(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际发送控制命令；不提供此参数时仅显示测试计划",
    )
    parser.add_argument(
        "--toggle-channel",
        type=int,
        choices=(1, 2),
        help="附加测试指定通道开关，完成后恢复原值",
    )
    args = parser.parse_args()

    if not args.execute:
        print("只读预览：将执行 STOP -> 查询 -> RUN -> 查询 -> 恢复原运行状态 -> 查询错误。")
        if args.toggle_channel:
            print(f"还将切换 CH{args.toggle_channel} 开关、查询确认并恢复原值。")
        print("确认示波器处于安全低压/无输入状态后，加 --execute 执行。")
        return 0

    scope = build_scope(args)
    results: dict[str, object] = {"resource": scope.resource, "steps": []}
    steps: list[dict[str, object]] = results["steps"]  # type: ignore[assignment]
    started = time.perf_counter()
    try:
        with scope:
            results["identity"] = public_identity(scope.identify(), args.show_serial)
            original_status = scope.query(":TRIG:STAT?").upper()
            original_running = original_status != "STOP"
            original_channel_enabled: bool | None = None
            steps.append({"read_original_status": original_status})
            try:
                scope.stop()
                time.sleep(0.2)
                steps.append({"stop_status": scope.query(":TRIG:STAT?")})

                scope.run()
                time.sleep(0.2)
                steps.append({"run_status": scope.query(":TRIG:STAT?")})

                if args.toggle_channel:
                    channel = args.toggle_channel
                    original_channel_enabled = scope.query(f":CHAN{channel}:DISP?") == "1"
                    scope.set_channel_enabled(channel, not original_channel_enabled)
                    changed = scope.query(f":CHAN{channel}:DISP?") == "1"
                    steps.append(
                        {
                            "channel": channel,
                            "original": original_channel_enabled,
                            "changed": changed,
                        }
                    )
            finally:
                if args.toggle_channel and original_channel_enabled is not None:
                    scope.set_channel_enabled(args.toggle_channel, original_channel_enabled)
                    steps.append(
                        {
                            "channel_restored": scope.query(
                                f":CHAN{args.toggle_channel}:DISP?"
                            )
                            == "1"
                        }
                    )
                if original_running:
                    scope.run()
                else:
                    scope.stop()
                steps.append({"restored_status": scope.query(":TRIG:STAT?")})
            results["errors"] = scope.get_errors()
        results["success"] = True
        results["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        print_json(results)
        return 0
    except Exception as exc:
        results["success"] = False
        results["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
        results["error"] = f"{type(exc).__name__}: {exc}"
        print_json(results)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
