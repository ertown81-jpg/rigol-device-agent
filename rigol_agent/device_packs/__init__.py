from .base import AdaptiveProfile, DeviceAdapter, DevicePack, DevicePackRegistry, validate_standard_result


def get_device_pack(pack_id: str = "rigol_ds1102ze") -> DevicePack:
    from .registry import get_device_pack as get_registered_pack

    return get_registered_pack(pack_id)


def list_device_packs() -> list[DevicePack]:
    from .registry import list_device_packs as list_registered_packs

    return list_registered_packs()


def register_device_pack(pack: DevicePack) -> None:
    from .registry import register_device_pack as register_pack

    register_pack(pack)

__all__ = [
    "AdaptiveProfile",
    "DeviceAdapter",
    "DevicePack",
    "DevicePackRegistry",
    "get_device_pack",
    "list_device_packs",
    "register_device_pack",
    "validate_standard_result",
]
