"""Reprise d'une installation « Cover Time Based » (cover_rf_time_based).

- détection des volets existants (temps de course, switch commandé, sens) ;
- détection des interrupteurs muraux reliés par des automatisations ;
- reprise des entity_id, noms, pièces, alias, labels et exposition vocale.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_AREA,
    CONF_COVER,
    CONF_EVENT,
    CONF_INVERTED,
    CONF_LEGACY,
    CONF_NAME,
    CONF_STOP_AT_ENDS,
    CONF_STOP_ENTITY,
    CONF_STOP_MODE,
    CONF_STOP_WHEN_MOVING,
    CONF_SWITCH,
    CONF_TIME_DOWN,
    CONF_TIME_UP,
    CONF_WALL_EVENT,
    CONF_WALL_MODE,
    LEGACY_PLATFORM,
    STOP_ENTITY,
    STOP_REPEAT,
    WALL_DIRECT,
    WALL_RELAY,
)

_LOGGER = logging.getLogger(__name__)
LEGACY_UID_PREFIX = "cover_rf_timebased_uuid_"


def legacy_entries(hass: HomeAssistant) -> list[er.RegistryEntry]:
    reg = er.async_get(hass)
    return [e for e in reg.entities.values() if e.platform == LEGACY_PLATFORM and e.domain == "cover"]


async def _legacy_yaml(hass: HomeAssistant) -> dict[str, dict]:
    """unique_id -> configuration YAML du volet."""
    out: dict[str, dict] = {}
    try:
        from homeassistant import config as conf_util

        conf = await conf_util.async_hass_config_yaml(hass)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Lecture de la configuration YAML impossible : %s", err)
        return out
    covers = conf.get("cover") or []
    if isinstance(covers, dict):
        covers = [covers]
    for plat in covers:
        if not isinstance(plat, dict) or plat.get("platform") != LEGACY_PLATFORM:
            continue
        for dev_id, cfg in (plat.get("devices") or {}).items():
            if isinstance(cfg, dict):
                out[LEGACY_UID_PREFIX + str(dev_id)] = cfg
    return out


def _script_raw(hass: HomeAssistant, script_id: str) -> Any:
    try:
        ent = hass.data["script"].get_entity(script_id)
        return getattr(ent, "raw_config", None)
    except Exception:  # noqa: BLE001
        return None


def _switch_of_script(hass: HomeAssistant, script_id: str | None) -> tuple[str | None, str | None]:
    """(switch commandé, service utilisé) par un script d'ouverture."""
    if not script_id:
        return None, None
    try:
        from homeassistant.components.script import entities_in_script

        ents = [e for e in entities_in_script(hass, script_id) if e.split(".", 1)[0] in ("switch", "light", "input_boolean")]
    except Exception:  # noqa: BLE001
        ents = []
    raw = str(_script_raw(hass, script_id) or "")
    service = "turn_off" if "turn_off" in raw and "turn_on" not in raw else "turn_on"
    return (ents[0] if ents else None), service


def _event_of_device(hass: HomeAssistant, device_id: str) -> str | None:
    reg = er.async_get(hass)
    for ent in er.async_entries_for_device(reg, device_id):
        if ent.domain == "event":
            return ent.entity_id
    return None


def _wall_links(hass: HomeAssistant) -> tuple[dict[str, dict], list[str]]:
    """Automatisations « interrupteur mural -> volet ».

    Renvoie {cover_entity_id: {event, device, automations}} et la liste des
    automatisations concernées (à désactiver une fois reprises).
    """
    try:
        from homeassistant.components.automation import devices_in_automation, entities_in_automation
    except Exception:  # noqa: BLE001
        return {}, []
    ent_reg = er.async_get(hass)
    links: dict[str, dict] = {}
    autos: list[str] = []
    for st in hass.states.async_all("automation"):
        aid = st.entity_id
        raw = _automation_raw(hass, aid)
        if not isinstance(raw, dict):
            continue
        try:
            devices = devices_in_automation(hass, aid)
            entities = entities_in_automation(hass, aid)
        except Exception:  # noqa: BLE001
            continue
        triggers = raw.get("triggers") or raw.get("trigger") or []
        if isinstance(triggers, dict):
            triggers = [triggers]
        trig_devices = {t.get("device_id") for t in triggers if isinstance(t, dict) and t.get("device_id")}
        events = [(d, ev) for d in trig_devices if (ev := _event_of_device(hass, d))]
        if len(events) != 1 or len(triggers) != 1:
            continue
        trig_dev, event_id = events[0]
        covers = {e for e in entities if e.startswith("cover.")}
        for d in devices:
            if d != trig_dev:
                covers |= {e.entity_id for e in er.async_entries_for_device(ent_reg, d) if e.domain == "cover"}
        if len(covers) != 1:
            continue
        cover = covers.pop()
        link = links.setdefault(cover, {"event": event_id, "device": trig_dev, "automations": []})
        if link["event"] != event_id:
            continue
        link["automations"].append(aid)
        autos.append(aid)
    return links, autos


def _automation_raw(hass: HomeAssistant, automation_id: str) -> Any:
    try:
        ent = hass.data["automation"].get_entity(automation_id)
        return getattr(ent, "raw_config", None)
    except Exception:  # noqa: BLE001
        return None


async def async_find_legacy(hass: HomeAssistant) -> tuple[list[dict], list[dict], list[str]]:
    """Détecte l'existant : (volets, liaisons, automatisations remplacées)."""
    yaml_cfg = await _legacy_yaml(hass)
    links, _ = _wall_links(hass)
    ent_reg = er.async_get(hass)
    volets: list[dict] = []
    used_autos: list[str] = []
    for ent in legacy_entries(hass):
        st = hass.states.get(ent.entity_id)
        attrs = st.attributes if st else {}
        cfg = yaml_cfg.get(ent.unique_id, {})
        switch, service = _switch_of_script(hass, cfg.get("open_script_entity_id"))
        if switch is None:
            _LOGGER.info("%s : switch commandé introuvable, volet ignoré", ent.entity_id)
            continue
        stop_switch, _ = _switch_of_script(hass, cfg.get("stop_script_entity_id"))
        data: dict[str, Any] = {
            CONF_NAME: ent.name or ent.original_name or (st.name if st else ent.entity_id),
            CONF_SWITCH: switch,
            CONF_INVERTED: service == "turn_off",
            CONF_TIME_UP: float(cfg.get("travelling_time_up") or attrs.get("travelling_time_up") or 20),
            CONF_TIME_DOWN: float(cfg.get("travelling_time_down") or attrs.get("travelling_time_down") or 20),
            CONF_STOP_AT_ENDS: bool(cfg.get("send_stop_at_ends", True)),
            CONF_LEGACY: ent.entity_id,
        }
        if stop_switch in (None, switch):
            data[CONF_STOP_MODE] = STOP_REPEAT
        else:
            data[CONF_STOP_MODE] = STOP_ENTITY
            data[CONF_STOP_ENTITY] = cfg.get("stop_script_entity_id")
        if ent.area_id:
            data[CONF_AREA] = ent.area_id
        link = links.pop(ent.entity_id, None)
        if link:
            sw_ent = ent_reg.async_get(switch)
            same_device = sw_ent is not None and sw_ent.device_id == link["device"]
            data[CONF_WALL_EVENT] = link["event"]
            data[CONF_WALL_MODE] = WALL_DIRECT if same_device else WALL_RELAY
            used_autos += link["automations"]
        volets.append(data)

    liaisons: list[dict] = []
    for cover, link in links.items():
        st = hass.states.get(cover)
        ev = hass.states.get(link["event"])
        liaisons.append({
            CONF_NAME: f"{ev.name if ev else link['event']} → {st.name if st else cover}",
            CONF_EVENT: link["event"],
            CONF_COVER: cover,
            CONF_STOP_WHEN_MOVING: True,
        })
        used_autos += link["automations"]
    return volets, liaisons, sorted(set(used_autos))


async def async_take_over_legacy(hass: HomeAssistant, entry: ConfigEntry, sub: ConfigSubentry, domain: str, unique_id: str) -> None:
    """Donne à la nouvelle entité l'entity_id et les réglages de l'ancienne."""
    legacy = sub.data.get(CONF_LEGACY)
    if not legacy:
        return
    reg = er.async_get(hass)
    if reg.async_get_entity_id(domain, entry.domain, unique_id):
        return  # déjà repris
    old = reg.async_get(legacy)
    if old is None or old.platform == entry.domain:
        return
    keep = {
        "name": old.name,
        "icon": old.icon,
        "aliases": old.aliases,
        "area_id": old.area_id,
        "labels": old.labels,
        "device_class": old.device_class,
        "hidden_by": old.hidden_by,
        "categories": old.categories,
    }
    options = {k: dict(v) for k, v in (old.options or {}).items()}
    object_id = legacy.split(".", 1)[1]
    reg.async_remove(legacy)
    new = reg.async_get_or_create(
        domain, entry.domain, unique_id,
        suggested_object_id=object_id, config_entry=entry, config_subentry_id=sub.subentry_id,
    )
    if new.entity_id != legacy and reg.async_get(legacy) is None:
        new = reg.async_update_entity(new.entity_id, new_entity_id=legacy)
    reg.async_update_entity(new.entity_id, **{k: v for k, v in keep.items() if v is not None})
    for opt_domain, opts in options.items():
        try:
            reg.async_update_entity_options(new.entity_id, opt_domain, opts)
        except Exception:  # noqa: BLE001
            pass
    _LOGGER.info("Volets HOLM : %s repris depuis %s", new.entity_id, LEGACY_PLATFORM)
