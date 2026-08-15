from __future__ import annotations

from .base import DevicePack, DevicePackRegistry
from .rigol_ds1102ze import RIGOL_DS1102ZE_PACK


_REGISTRY = DevicePackRegistry()
_REGISTRY.register(RIGOL_DS1102ZE_PACK)


def get_device_pack(pack_id: str = "rigol_ds1102ze") -> DevicePack:
    return _REGISTRY.get(pack_id)


def list_device_packs() -> list[DevicePack]:
    return _REGISTRY.list()


def register_device_pack(pack: DevicePack) -> None:
    """Registration is explicit so unreviewed files cannot gain hardware access."""

    _REGISTRY.register(pack)
