from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from .device_packs import DevicePack, get_device_pack, list_device_packs


class DeviceSwitchUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentStack:
    pack: DevicePack
    adapter: Any
    tools: Any
    agent: Any


class DeviceRuntime:
    """Owns one active device stack and swaps it atomically after a new stack builds."""

    def __init__(
        self,
        stack: AgentStack,
        *,
        stack_factory: Callable[[DevicePack], AgentStack] | None = None,
        pack_getter: Callable[[str], DevicePack] = get_device_pack,
        pack_lister: Callable[[], list[DevicePack]] = list_device_packs,
    ) -> None:
        self._stack = stack
        self._stack_factory = stack_factory
        self._pack_getter = pack_getter
        self._pack_lister = pack_lister
        self._lock = RLock()

    def snapshot(self) -> AgentStack:
        with self._lock:
            return self._stack

    def describe(self) -> dict[str, Any]:
        stack = self.snapshot()
        return {
            "active_pack_id": stack.pack.pack_id,
            "switching_supported": self._stack_factory is not None,
            "device_packs": [pack.metadata() for pack in self._pack_lister()],
        }

    def select(self, pack_id: str) -> dict[str, Any]:
        selected = self._pack_getter(pack_id)
        current = self.snapshot()
        if selected.pack_id == current.pack.pack_id:
            return {"changed": False, "active_device_pack": current.pack.metadata()}
        if self._stack_factory is None:
            raise DeviceSwitchUnavailable("当前服务启动方式不支持运行时切换设备包")

        # Build first. If construction fails, the active device remains untouched.
        replacement = self._stack_factory(selected)
        if replacement.pack.pack_id != selected.pack_id:
            replacement.adapter.close()
            raise RuntimeError("设备运行时工厂返回了错误的设备包")
        with self._lock:
            previous = self._stack
            self._stack = replacement
        close_warning = None
        try:
            previous.adapter.close()
        except Exception as exc:
            close_warning = f"原设备连接关闭失败: {type(exc).__name__}: {exc}"
        return {
            "changed": True,
            "active_device_pack": replacement.pack.metadata(),
            "warning": close_warning,
        }

    def close(self) -> None:
        self.snapshot().adapter.close()
