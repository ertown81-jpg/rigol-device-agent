from __future__ import annotations

import argparse
from pathlib import Path

from common import add_connection_arguments, build_scope, print_json, public_identity
from src.utils import save_json, timestamp_for_filename
from src.waveform import save_csv


def save_plot(time_s: list[float], voltage_v: list[float], output: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "绘图需要可选依赖：python -m pip install -r requirements-plot.txt"
        ) from exc
    figure, axes = plt.subplots(figsize=(10, 4.5))
    axes.plot(time_s, voltage_v, linewidth=0.8)
    axes.set_xlabel("Time (s)")
    axes.set_ylabel("Voltage (V)")
    axes.grid(True, alpha=0.3)
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description="采集波形并导出 CSV 与元数据 JSON")
    add_connection_arguments(parser)
    parser.add_argument("--channel", type=int, choices=(1, 2), default=1)
    parser.add_argument("--mode", choices=("NORMAL", "RAW"), default="NORMAL")
    parser.add_argument("--max-points", type=int, help="限制读取点数，便于快速演示")
    parser.add_argument("--output", help="CSV 输出路径")
    parser.add_argument("--plot", action="store_true", help="同时生成 PNG 波形图")
    args = parser.parse_args()

    stamp = timestamp_for_filename()
    csv_path = Path(args.output or f"output/waveform_ch{args.channel}_{stamp}.csv")
    metadata_path = csv_path.with_suffix(".json")
    plot_path = csv_path.with_suffix(".png")
    scope = build_scope(args)

    try:
        with scope:
            identity = public_identity(scope.identify(), args.show_serial)
            status = scope.get_status()
            capture = scope.capture_waveform(
                args.channel,
                mode=args.mode,
                max_points=args.max_points,
            )
            errors = scope.get_errors()
        save_csv(capture, csv_path)
        metadata = {
            "success": True,
            "resource": scope.resource,
            "identity": identity,
            "capture": capture.summary(),
            "sample_rate_sa_s": status["acquisition"]["sample_rate_sa_s"],
            "timebase": status["timebase"],
            "channel_config": status["channels"][f"CH{args.channel}"],
            "trigger": status["trigger"],
            "errors": errors,
            "csv_path": str(csv_path),
        }
        if args.plot:
            save_plot(capture.time_s, capture.voltage_v, plot_path)
            metadata["plot_path"] = str(plot_path)
        save_json(metadata_path, metadata)
        metadata["metadata_path"] = str(metadata_path)
        print_json(metadata)
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
