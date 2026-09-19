"""Local BLE control of Aqara HMCN02 curtains."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_LTMK, DEFAULT_NAME
from .coordinator import CurtainCoordinator

type CurtainConfigEntry = ConfigEntry[CurtainCoordinator]
PLATFORMS = [Platform.COVER]


async def async_setup_entry(hass: HomeAssistant, entry: CurtainConfigEntry) -> bool:
    """Authenticate and read state before adding the cover."""
    if entry.title == "Lumi Curtain":
        hass.config_entries.async_update_entry(entry, title=DEFAULT_NAME)
    coordinator = CurtainCoordinator(
        hass, entry.data["address"], entry.data[CONF_LTMK], entry
    )
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CurtainConfigEntry) -> bool:
    """Release the active connection after the entities unload."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
        return True
    return False
