"""Diagnostics without Bluetooth identifiers or authentication secrets."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import CurtainConfigEntry
from .const import MODEL


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: CurtainConfigEntry,
) -> dict[str, Any]:
    """Export only model, availability and confirmed curtain state."""
    coordinator = entry.runtime_data
    return {
        "model": MODEL,
        "available": coordinator.last_update_success,
        "state": {
            f"{siid}.{piid}": value
            for (siid, piid), value in (coordinator.data or {}).items()
            if siid != 1
        },
    }
