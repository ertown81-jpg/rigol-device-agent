from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from .agent import RigolAgent
from .adaptive import ClosedLoopSignalAgent
from .device_packs import DevicePack, get_device_pack
from .model_planner import build_planner, planner_status
from .policy import ExecutionPolicy
from .runtime import AgentStack, DeviceRuntime, DeviceSwitchUnavailable
from .tools import ToolRegistry

WEB_ROOT = Path(__file__).with_name("web")
ARTIFACT_ROOT = Path("output/agent").resolve()


def serve(
    adapter: Any,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_changes: bool = False,
    allow_adaptive_changes: bool = False,
    planner_name: str = "auto",
    model: str | None = None,
    device_pack: DevicePack | None = None,
    adapter_factory: Callable[[DevicePack], Any] | None = None,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("设备 Agent API 只能监听本机回环地址")
    pack = device_pack or get_device_pack()
    stack = _build_stack(
        adapter,
        pack,
        allow_changes=allow_changes,
        allow_adaptive_changes=allow_adaptive_changes,
        planner_name=planner_name,
        model=model,
    )

    def build_replacement(selected: DevicePack) -> AgentStack:
        if adapter_factory is None:
            raise DeviceSwitchUnavailable("服务没有配置设备适配器工厂")
        selected_adapter = adapter_factory(selected)
        try:
            return _build_stack(
                selected_adapter,
                selected,
                allow_changes=allow_changes,
                allow_adaptive_changes=allow_adaptive_changes,
                planner_name=planner_name,
                model=model,
            )
        except Exception:
            selected_adapter.close()
            raise

    runtime = DeviceRuntime(stack, stack_factory=build_replacement if adapter_factory else None)
    handler = _handler_factory(runtime=runtime)
    server = HTTPServer((host, port), handler)
    print(f"Device Agent [{pack.pack_id}]: http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.close()


def _build_stack(
    adapter: Any,
    pack: DevicePack,
    *,
    allow_changes: bool,
    allow_adaptive_changes: bool,
    planner_name: str,
    model: str | None,
) -> AgentStack:
    tools = ToolRegistry(
        adapter,
        ExecutionPolicy(
            allow_changes=allow_changes,
            device_label=pack.display_name,
            argument_validator=pack.argument_validator,
        ),
        pack.tool_specs,
        pack.result_validator,
    )
    agent = RigolAgent(
        build_planner(
            planner_name,
            tools,
            model,
            system_instructions=pack.planner_instructions,
            fallback=pack.rule_planner_factory(),
        ),
        tools,
    )
    if pack.adaptive is not None and pack.adaptive.kind == "oscilloscope_signal":
        agent.adaptive_runner = ClosedLoopSignalAgent(
            tools,
            agent.planner,
            max_rounds=pack.adaptive.max_rounds,
            allowed_change_tools=pack.adaptive.change_tools,
            allow_adaptive_changes=allow_adaptive_changes,
        )
    return AgentStack(pack=pack, adapter=adapter, tools=tools, agent=agent)


def _handler_factory(
    agent: RigolAgent | None = None,
    tools: ToolRegistry | None = None,
    device_pack: DevicePack | None = None,
    *,
    runtime: DeviceRuntime | None = None,
) -> type[BaseHTTPRequestHandler]:
    if runtime is None:
        if agent is None or tools is None:
            raise ValueError("必须提供 runtime，或同时提供 agent 和 tools")
        pack = device_pack or get_device_pack()
        runtime = DeviceRuntime(AgentStack(pack=pack, adapter=tools.adapter, tools=tools, agent=agent))

    class AgentHandler(BaseHTTPRequestHandler):
        server_version = "DeviceAgent/4.1"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            stack = runtime.snapshot()
            agent = stack.agent
            tools = stack.tools
            pack = stack.pack
            adaptive_runner = getattr(agent, "adaptive_runner", None)
            if path == "/":
                self._file(WEB_ROOT / "index.html")
            elif path in {"/app.js", "/styles.css"}:
                self._file(WEB_ROOT / path[1:])
            elif path == "/health":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "device_pack": pack.metadata(),
                        "planner": planner_status(agent.planner),
                        "adaptive": {
                            "enabled": adaptive_runner is not None,
                            "max_rounds": adaptive_runner.max_rounds if adaptive_runner is not None else 0,
                            "authorized_changes": list(pack.adaptive.change_tools) if pack.adaptive else [],
                            "change_lease_granted": bool(adaptive_runner and adaptive_runner.allow_adaptive_changes),
                        },
                    },
                )
            elif path == "/capabilities":
                self._json(
                    HTTPStatus.OK,
                    {
                        "tools": tools.capabilities(),
                        "device_pack": pack.metadata(),
                        "adaptive_signal_analysis": {
                            "enabled": adaptive_runner is not None,
                            "max_rounds": adaptive_runner.max_rounds if adaptive_runner is not None else 0,
                            "restores_original_settings": True,
                            "requires_explicit_change_lease": True,
                            "raw_scpi": False,
                        },
                    },
                )
            elif path == "/device-packs":
                self._json(HTTPStatus.OK, runtime.describe())
            elif path == "/device":
                try:
                    self._json(HTTPStatus.OK, tools.execute("get_device_status", {}))
                except Exception as exc:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"online": False, "error": f"{type(exc).__name__}: {exc}"})
            elif path == "/sessions":
                sessions = []
                session_dir = ARTIFACT_ROOT / "sessions"
                for item in sorted(session_dir.glob("*.json"), key=lambda value: value.stat().st_mtime, reverse=True)[:20]:
                    try:
                        data = json.loads(item.read_text(encoding="utf-8"))
                        analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
                        quality = analysis.get("quality") if isinstance(analysis.get("quality"), dict) else {}
                        sessions.append(
                            {
                                **{key: data.get(key) for key in ("session_id", "request", "success", "started_at", "summary", "report_path")},
                                "execution_success": analysis.get("execution_success", data.get("success")),
                                "scientific_success": analysis.get("scientific_success"),
                                "settings_restored": analysis.get("settings_restored"),
                                "restoration_status": analysis.get("restoration_status"),
                                "quality_level": quality.get("level"),
                                "stop_code": analysis.get("stopping_reason"),
                            }
                        )
                    except (OSError, json.JSONDecodeError):
                        continue
                self._json(HTTPStatus.OK, {"sessions": sessions})
            elif path.startswith("/artifacts/"):
                relative = Path(*path.removeprefix("/artifacts/").split("/"))
                target = (ARTIFACT_ROOT / relative).resolve()
                if target == ARTIFACT_ROOT or ARTIFACT_ROOT not in target.parents:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                else:
                    self._file(target)
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            try:
                payload = self._read_json()
                if self.path == "/device-packs/select":
                    pack_id = str(payload.get("pack_id", "")).strip()
                    if not pack_id:
                        raise ValueError("pack_id 不能为空")
                    try:
                        selection = runtime.select(pack_id)
                    except (KeyError, DeviceSwitchUnavailable):
                        raise
                    except Exception as exc:
                        self._json(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            {"error": f"新设备连接失败，当前设备保持不变: {type(exc).__name__}: {exc}"},
                        )
                        return
                    self._json(HTTPStatus.OK, selection)
                    return
                request = str(payload.get("request", "")).strip()
                if not request:
                    raise ValueError("request 不能为空")
                stack = runtime.snapshot()
                agent = stack.agent
                if self.path == "/plan":
                    self._json(HTTPStatus.OK, agent.plan(request).to_dict())
                elif self.path == "/tasks":
                    result = agent.run(request)
                    execution_success = result.analysis.get("execution_success", result.success)
                    status = HTTPStatus.OK if execution_success else HTTPStatus.UNPROCESSABLE_ENTITY
                    self._json(status, result.to_dict())
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except KeyError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except DeviceSwitchUnavailable as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("请求体大小无效")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            return payload

        def _json(self, status: HTTPStatus, payload: Any) -> None:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path: Path) -> None:
            if not path.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            data = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK.value)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"{self.address_string()} {format % args}")

    return AgentHandler
