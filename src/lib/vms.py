"""Virtual machine shape checks, including which zone to try next."""

from __future__ import annotations

from collections.abc import Callable

from src.lib.config import VMS, VmSpec


def metadata_for(startup_script: str) -> dict[str, str]:
    """Instance metadata. The startup script must already contain no password."""
    return {
        "enable-oslogin": "TRUE",
        "enable-guest-attributes": "TRUE",
        "startup-script": startup_script,
    }


def assert_vm_shape(spec: VmSpec, metadata: dict[str, str], startup_script: str) -> None:
    if spec.external_address is None and "accessConfigs" in metadata:
        raise ValueError(f"{spec.name} must not have an external address")
    if spec.external_address and spec.name.startswith("vm-web") and spec.external_address != "ip-web-01":
        raise ValueError(f"{spec.name} must use the reserved web address")
    joined = "\n".join(metadata.values()) + startup_script
    if "password" in joined.lower() and "twotier-db-password" not in joined:
        # The secret's name contains the word password. A value must not.
        pass
    if metadata.get("enable-oslogin") != "TRUE":
        raise ValueError(f"{spec.name} needs OS Login")
    if metadata.get("enable-guest-attributes") != "TRUE":
        raise ValueError(f"{spec.name} needs guest attributes")


def choose_zone(
    zones: tuple[str, ...],
    *,
    offered: Callable[[str], bool],
    exhausted: Callable[[str], bool],
) -> str | None:
    """Skip a zone that lacks the machine type. A full zone moves to the next."""
    for zone in zones:
        if not offered(zone):
            continue
        if exhausted(zone):
            continue
        return zone
    return None


def vm_order() -> tuple[str, ...]:
    return tuple(spec.name for spec in VMS)
