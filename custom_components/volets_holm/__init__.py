"""Volets HOLM : volets temporisés (RFXCOM et autres) et interrupteurs muraux, tout depuis l'interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, LEGACY_PLATFORM, SUB_LIAISON
from .links import WallLink
from .migrate import async_find_legacy, legacy_entries

PLATFORMS = [Platform.COVER]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class Hub:
    volets: dict[str, Any] = field(default_factory=dict)
    links: list[WallLink] = field(default_factory=list)


type VoletsConfigEntry = ConfigEntry[Hub]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _detect(call: ServiceCall) -> ServiceResponse:
        volets, liaisons, autos = await async_find_legacy(hass)
        return {"volets": volets, "liaisons": liaisons, "automations": autos}

    hass.services.async_register(DOMAIN, "detect_legacy", _detect, supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: VoletsConfigEntry) -> bool:
    hub = Hub()
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    for sub in entry.subentries.values():
        if sub.subentry_type == SUB_LIAISON:
            link = WallLink(hass, dict(sub.data))
            link.start()
            hub.links.append(link)

    _check_legacy(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VoletsConfigEntry) -> bool:
    for link in entry.runtime_data.links:
        link.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: VoletsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _check_legacy(hass: HomeAssistant) -> None:
    """Signale si l'ancienne configuration YAML est encore chargée."""
    live = [e.entity_id for e in legacy_entries(hass) if hass.states.get(e.entity_id) is not None]
    if live:
        ir.async_create_issue(
            hass, DOMAIN, "legacy_yaml", is_fixable=False, severity=ir.IssueSeverity.WARNING,
            translation_key="legacy_yaml", translation_placeholders={"platform": LEGACY_PLATFORM, "entities": ", ".join(live)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "legacy_yaml")
