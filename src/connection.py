from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


RIGOL_USB_VENDOR_ID = "0x1ab1"


def make_lan_resource(ip: str) -> str:
    ip = ip.strip()
    if not ip:
        raise ValueError("LAN IP 地址不能为空")
    return f"TCPIP0::{ip}::INSTR"


def is_rigol_resource(resource: str) -> bool:
    return RIGOL_USB_VENDOR_ID in resource.lower()


def create_resource_manager(backend: str | None = None) -> Any:
    try:
        import pyvisa
    except ImportError as exc:
        raise RuntimeError(
            "缺少 PyVISA。请先执行: python -m pip install -r requirements.txt"
        ) from exc
    return pyvisa.ResourceManager(backend or "")


def discover_resources(
    backend: str | None = None,
    extra_resources: Iterable[str] = (),
) -> list[str]:
    manager = create_resource_manager(backend)
    try:
        discovered = list(manager.list_resources())
    finally:
        manager.close()
    for resource in extra_resources:
        if resource and resource not in discovered:
            discovered.append(resource)
    return discovered


@dataclass
class VisaConnection:
    resource_name: str
    backend: str | None = None
    timeout_ms: int = 10_000
    query_delay_s: float = 0.05
    chunk_size: int = 1_048_576

    resource_manager: Any | None = None
    instrument: Any | None = None

    @property
    def connected(self) -> bool:
        return self.instrument is not None

    def open(self) -> Any:
        if self.connected:
            return self.instrument
        self.resource_manager = create_resource_manager(self.backend)
        try:
            self.instrument = self.resource_manager.open_resource(self.resource_name)
            self.instrument.timeout = self.timeout_ms
            self.instrument.query_delay = self.query_delay_s
            self.instrument.chunk_size = self.chunk_size
            self.instrument.write_termination = "\n"
            self.instrument.read_termination = "\n"
        except Exception:
            self.close()
            raise
        return self.instrument

    def close(self) -> None:
        instrument, manager = self.instrument, self.resource_manager
        self.instrument = None
        self.resource_manager = None
        if instrument is not None:
            try:
                instrument.close()
            except Exception:
                pass
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass

    def __enter__(self) -> "VisaConnection":
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
