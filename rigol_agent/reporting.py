from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any

from src.utils import ensure_parent
from src.waveform import WaveformCapture

from .models import TaskResult


def save_waveform_svg(capture: WaveformCapture, path: str | Path) -> Path:
    output = ensure_parent(path)
    width, height, padding = 1000, 360, 45
    times = capture.time_s
    volts = capture.voltage_v
    if not times or not volts:
        raise ValueError("没有可绘制的波形数据")
    stride = max(1, len(times) // 2000)
    points = list(zip(times[::stride], volts[::stride]))
    x_min, x_max = min(times), max(times)
    y_min, y_max = min(volts), max(volts)
    if x_max == x_min:
        x_max = x_min + 1.0
    if y_max == y_min:
        y_min -= 0.5
        y_max += 0.5

    def project(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        px = padding + (x - x_min) / (x_max - x_min) * (width - 2 * padding)
        py = height - padding - (y - y_min) / (y_max - y_min) * (height - 2 * padding)
        return px, py

    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in map(project, points))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#111827"/>
<g stroke="#374151" stroke-width="1">
  <path d="M {padding} {height / 2} H {width - padding}"/>
  <path d="M {width / 2} {padding} V {height - padding}"/>
</g>
<polyline fill="none" stroke="#facc15" stroke-width="1.5" points="{polyline}"/>
<g fill="#d1d5db" font-family="Arial, sans-serif" font-size="13">
  <text x="{padding}" y="24">CH{capture.channel} {html.escape(capture.mode)} · {capture.points} points</text>
  <text x="{padding}" y="{height - 12}">{x_min:.6g} s</text>
  <text x="{width - padding}" y="{height - 12}" text-anchor="end">{x_max:.6g} s</text>
  <text x="8" y="{padding}">{y_max:.6g} V</text>
  <text x="8" y="{height - padding}">{y_min:.6g} V</text>
</g>
</svg>
"""
    output.write_text(svg, encoding="utf-8")
    return output


def save_html_report(result: TaskResult, path: str | Path) -> Path:
    output = ensure_parent(path)
    execution_success = bool(result.analysis.get("execution_success", result.success))
    scientific_success = result.analysis.get("scientific_success")
    settings_restored = result.analysis.get("settings_restored")
    status_class = "ok" if execution_success else "failed"
    status_text = "执行成功" if execution_success else "执行失败"
    scientific_class = "ok" if scientific_success is True else "warn" if scientific_success is False else "neutral"
    scientific_text = "科学结论可信" if scientific_success is True else "科学证据不足" if scientific_success is False else "未进行科学判定"
    restore_class = "ok" if settings_restored is True else "failed" if settings_restored is False else "neutral"
    restore_text = "设置已恢复" if settings_restored is True else "设置恢复失败" if settings_restored is False else "无设置恢复要求"
    steps = "".join(
        f"<li><code>{html.escape(step.tool)}</code> — {html.escape(step.reason)}</li>"
        for step in result.plan.steps
    ) or "<li>没有执行任何工具</li>"
    cards = "".join(_result_card(item.to_dict(), output.parent) for item in result.results)
    adaptive = _adaptive_section(result.analysis)
    planning = html.escape(json.dumps(result.plan.planning, ensure_ascii=False, indent=2, default=str))
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RIGOL Agent 任务报告</title>
<style>
body{{font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif;max-width:1050px;margin:36px auto;padding:0 24px;color:#1f2937;background:#f8fafc}}
h1{{margin-bottom:6px}} h2{{margin-top:28px}} .meta{{color:#64748b}} .badge{{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:700}}
.ok{{background:#dcfce7;color:#166534}} .warn{{background:#fef3c7;color:#92400e}} .failed{{background:#fee2e2;color:#991b1b}} .neutral{{background:#e2e8f0;color:#334155}} .card{{background:white;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin:12px 0}}
code,pre{{font-family:Consolas,monospace}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:12px;border-radius:6px}}
img{{max-width:100%;height:auto;border:1px solid #cbd5e1}} a{{color:#0369a1}}
</style></head><body>
<h1>RIGOL Agent 任务报告</h1>
<p><span class="badge {status_class}">{status_text}</span> <span class="badge {scientific_class}">{scientific_text}</span> <span class="badge {restore_class}">{restore_text}</span></p>
<p><strong>任务：</strong>{html.escape(result.request)}</p>
<p class="meta">会话 {html.escape(result.session_id)} · {html.escape(result.started_at)} 至 {html.escape(result.finished_at)}</p>
<p>{html.escape(result.summary)}</p>
<section class="card"><h2>Agent 结论</h2><p>{html.escape(result.analysis.get('conclusion', ''))}</p></section>
{adaptive}
<h2>执行计划</h2><ol>{steps}</ol>
<section class="card"><h3>规划证据</h3><pre>{planning}</pre></section>
<h2>执行结果</h2>{cards}
</body></html>"""
    output.write_text(document, encoding="utf-8")
    return output


def _result_card(item: dict[str, Any], report_dir: Path) -> str:
    success = bool(item["success"])
    heading = f"{html.escape(item['tool'])} · {'成功' if success else '失败'}"
    body = html.escape(json.dumps(item.get("data") if success else item.get("error"), ensure_ascii=False, indent=2, default=str))
    links: list[str] = []
    data = item.get("data")
    if isinstance(data, dict):
        for key in ("csv_path", "metadata_path", "plot_path", "output"):
            value = data.get(key)
            if not value:
                continue
            target = Path(str(value))
            relative = os.path.relpath(target.resolve(), report_dir.resolve()).replace("\\", "/")
            links.append(f'<a href="{html.escape(relative)}">{html.escape(key)}</a>')
            if target.suffix.lower() in {".png", ".svg"} and target.exists():
                links.append(f'<div><img src="{html.escape(relative)}" alt="{html.escape(key)}"></div>')
    extras = " · ".join(links)
    return f'<section class="card"><h3>{heading}</h3><p>{extras}</p><pre>{body}</pre></section>'


def _adaptive_section(analysis: dict[str, Any]) -> str:
    iterations = analysis.get("adaptive_iterations")
    if not isinstance(iterations, list) or not iterations:
        return ""
    quality = analysis.get("quality", {})
    objective = analysis.get("objective", {})
    stop = analysis.get("stop", {})
    final_hypotheses = analysis.get("final_hypotheses", [])
    criteria = "".join(
        f"<li>{'✓' if item.get('met') else '✗'} {html.escape(str(item.get('label', '')))}：{html.escape(str(item.get('observed', '')))}</li>"
        for item in objective.get("success_criteria", [])
    )
    hypotheses = "".join(
        f"<li><strong>{html.escape(str(item.get('label', '')))}</strong> · {float(item.get('score') or 0):.0%} · {html.escape(str(item.get('status', '')))}</li>"
        for item in final_hypotheses[:5]
    )
    rows: list[str] = []
    for item in iterations:
        observation = item.get("observation", {})
        measurements = observation.get("measurements", {})
        waveform = observation.get("waveform", {})
        decision = item.get("decision", {})
        experiment = decision.get("experiment", {})
        planning = decision.get("planning", {})
        actions = ", ".join(action.get("tool", "") for action in decision.get("actions", [])) or "完成判断"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('round')))}</td>"
            f"<td>{html.escape(str(observation.get('signal_class', '')))}</td>"
            f"<td>{html.escape(str(measurements.get('frequency_hz')))}</td>"
            f"<td>{html.escape(str(measurements.get('vpp_v')))}</td>"
            f"<td>{html.escape(str(measurements.get('rms_v')))}</td>"
            f"<td>{html.escape(str(waveform.get('robust_span_v')))}</td>"
            f"<td><strong>{html.escape(str(experiment.get('question') or item.get('round_goal') or ''))}</strong><br>{html.escape(str(decision.get('assessment', '')))}<br><code>{html.escape(actions)}</code></td>"
            f"<td>{html.escape(str(decision.get('source', '')))}<br>{html.escape(str(planning.get('duration_ms', '')))} ms</td>"
            "</tr>"
        )
    reasons = "；".join(str(item) for item in quality.get("reasons", []))
    return (
        '<section class="card"><h2>闭环分析过程</h2>'
        f"<h3>实验目标</h3><p>{html.escape(str(objective.get('statement', '')))}</p><ul>{criteria}</ul>"
        f"<h3>最终竞争假设</h3><ol>{hypotheses}</ol>"
        f"<p>科学结论置信度：<strong>{html.escape(str(quality.get('level', '')))}</strong> "
        f"({html.escape(str(quality.get('score', '')))})；{html.escape(reasons)}</p>"
        '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        '<thead><tr><th>轮次</th><th>判断</th><th>频率 Hz</th><th>Vpp V</th><th>RMS V</th><th>稳健幅度 V</th><th>决策</th><th>规划来源/耗时</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        f"<h3>停止原因</h3><p><code>{html.escape(str(stop.get('code', '')))}</code> · {html.escape(str(stop.get('reason', '')))}</p>"
        "</section>"
    )
