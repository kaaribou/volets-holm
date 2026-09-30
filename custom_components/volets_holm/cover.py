"""Volets temporisés : la position est calculée d'après le temps de course."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from time import monotonic
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_platform
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
import voluptuous as vol

from .const import (
    CONF_AREA,
    CONF_ENDSTOP_EXTRA,
    CONF_INVERTED,
    CONF_NAME,
    CONF_RECAL_FULL,
    CONF_STOP_AT_ENDS,
    CONF_STOP_ENTITY,
    CONF_STOP_MODE,
    CONF_SWITCH,
    CONF_TIME_DOWN,
    CONF_TIME_UP,
    CONF_WALL_EVENT,
    CONF_WALL_MODE,
    DOMAIN,
    EV_CLOSE,
    EV_OPEN,
    STOP_ENTITY,
    STOP_REPEAT,
    SUB_VOLET,
    TICK_SECONDS,
    VERSION,
    VOLET_DEFAULTS,
    WALL_DIRECT,
)
from .migrate import async_take_over_legacy
from .travel import DOWN, UP, Travel

_LOGGER = logging.getLogger(__name__)

OPEN, CLOSE = "open", "close"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    for sub in entry.subentries.values():
        if sub.subentry_type != SUB_VOLET:
            continue
        unique_id = f"{DOMAIN}_{sub.subentry_id}"
        await async_take_over_legacy(hass, entry, sub, "cover", unique_id)
        ent = VoletCover(entry, sub)
        hub.volets[sub.subentry_id] = ent
        async_add_entities([ent], config_subentry_id=sub.subentry_id)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "set_known_position", {vol.Required(ATTR_POSITION): vol.All(vol.Coerce(int), vol.Range(0, 100))}, "async_set_known_position",
    )
    platform.async_register_entity_service("recalibrate", {}, "async_recalibrate")


class VoletCover(CoverEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = None
    _attr_assumed_state = True
    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, entry: ConfigEntry, sub: ConfigSubentry) -> None:
        self.entry = entry
        self.sub = sub
        self.cfg: dict[str, Any] = {**VOLET_DEFAULTS, **sub.data}
        self._attr_unique_id = f"{DOMAIN}_{sub.subentry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sub.subentry_id)},
            name=self.cfg[CONF_NAME],
            manufacturer="HOLM",
            model="Volet temporisé",
            sw_version=VERSION,
        )
        self.travel = Travel(self.cfg[CONF_TIME_UP], self.cfg[CONF_TIME_DOWN])
        self._lock = asyncio.Lock()
        self._end_unsub = None
        self._tick_unsub = None
        self._last_cmd: str | None = None
        self._track_only = False
        self._run_target: float | None = None

    # ------------------------------------------------------------- cycle de vie
    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None and last.attributes.get(ATTR_CURRENT_POSITION) is not None:
            try:
                self.travel.set_position(float(last.attributes[ATTR_CURRENT_POSITION]))
            except (TypeError, ValueError):
                pass
        watch = [self.cfg[CONF_SWITCH]]
        if self.cfg.get(CONF_WALL_EVENT):
            self.async_on_remove(async_track_state_change_event(self.hass, [self.cfg[CONF_WALL_EVENT]], self._on_wall))
        self.async_on_remove(async_track_state_change_event(self.hass, watch, self._on_switch))
        self._apply_area()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_timers()

    @callback
    def _apply_area(self) -> None:
        area = self.cfg.get(CONF_AREA)
        if not area or self.registry_entry is None or not self.registry_entry.device_id:
            return
        dev_reg = dr.async_get(self.hass)
        dev = dev_reg.async_get(self.registry_entry.device_id)
        if dev is not None and dev.area_id is None:
            dev_reg.async_update_device(dev.id, area_id=area)

    # ------------------------------------------------------------------ état
    @property
    def available(self) -> bool:
        st = self.hass.states.get(self.cfg[CONF_SWITCH])
        return st is not None and st.state != STATE_UNAVAILABLE

    @property
    def current_cover_position(self) -> int:
        return int(round(self.travel.position(monotonic())))

    @property
    def is_opening(self) -> bool:
        return self.travel.direction == UP

    @property
    def is_closing(self) -> bool:
        return self.travel.direction == DOWN

    @property
    def is_closed(self) -> bool:
        return self.current_cover_position <= 0 and not self.travel.moving

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "travelling_time_up": self.cfg[CONF_TIME_UP],
            "travelling_time_down": self.cfg[CONF_TIME_DOWN],
        }

    # ------------------------------------------------------- commandes cover
    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._run(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._run(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._run(float(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        async with self._lock:
            await self._stop(send=True)

    async def async_set_known_position(self, position: int) -> None:
        """Indique la position réelle sans rien envoyer au moteur."""
        self._cancel_timers()
        self.travel.set_position(position)
        self._track_only = False
        self.async_write_ha_state()

    async def async_recalibrate(self) -> None:
        """Recale le volet en l'envoyant en butée du côté le plus proche."""
        await self._run(100 if self.current_cover_position >= 50 else 0, force_full=True)

    # ------------------------------------------------------------ mouvements
    async def _run(self, target: float, *, send: bool = True, force_full: bool = False) -> None:
        async with self._lock:
            now = monotonic()
            cur = self.travel.position(now)
            target = min(max(target, 0.0), 100.0)
            at_end = target in (0.0, 100.0)
            if not at_end and abs(target - cur) < 0.5 and not self.travel.moving:
                return
            direction = (UP if target == 100 else DOWN) if at_end else (UP if target > cur else DOWN)
            same_way = self.travel.moving and self.travel.direction == direction
            if at_end and abs(cur - target) < 0.5 and not self.travel.moving:
                # déjà en butée : on relance quand même pour se recaler
                self.travel.start(target, now, direction)
                full = self.travel.time_up if direction == UP else self.travel.time_down
                total = (full if (self.cfg[CONF_RECAL_FULL] or force_full) else 0.0) + self.cfg[CONF_ENDSTOP_EXTRA]
            else:
                if force_full:
                    self.travel.set_position(0 if direction == UP else 100)
                    now = monotonic()
                total = self.travel.start(target, now, direction) + (self.cfg[CONF_ENDSTOP_EXTRA] if at_end else 0.0)
            self._run_target = target
            cmd = OPEN if direction == UP else CLOSE
            if send and not same_way:
                self._track_only = False
                await self._cmd(cmd)
            elif not send:
                self._track_only = True
                self._last_cmd = cmd
            self._schedule_end(total)
            self.async_write_ha_state()

    async def _stop(self, *, send: bool) -> None:
        was_moving = self.travel.moving
        self._cancel_timers()
        self.travel.stop(monotonic())
        if send and (was_moving or self.cfg[CONF_STOP_MODE] == STOP_ENTITY):
            await self._send_stop()
        self._track_only = False
        self.async_write_ha_state()

    @callback
    def _schedule_end(self, delay: float) -> None:
        self._cancel_timers()
        self._end_unsub = async_call_later(self.hass, max(delay, 0.0), self._on_end)
        self._tick_unsub = async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=TICK_SECONDS))

    @callback
    def _cancel_timers(self) -> None:
        if self._end_unsub:
            self._end_unsub()
            self._end_unsub = None
        if self._tick_unsub:
            self._tick_unsub()
            self._tick_unsub = None

    @callback
    def _on_tick(self, _now) -> None:
        self.async_write_ha_state()

    @callback
    def _on_end(self, _now) -> None:
        self._end_unsub = None
        self.hass.async_create_task(self._finish(), eager_start=True)

    async def _finish(self) -> None:
        async with self._lock:
            target = self._run_target if self._run_target is not None else self.travel.target
            self._cancel_timers()
            self.travel.set_position(target)
            if not self._track_only and (0 < target < 100 or self.cfg[CONF_STOP_AT_ENDS]):
                await self._send_stop()
            self._track_only = False
            self._run_target = None
            self.async_write_ha_state()

    # ------------------------------------------------------ envoi au moteur
    async def _cmd(self, cmd: str) -> None:
        on = (cmd == OPEN) != bool(self.cfg[CONF_INVERTED])
        self._last_cmd = cmd
        await self._switch(on)

    async def _switch(self, on: bool) -> None:
        sw = self.cfg[CONF_SWITCH]
        await self._call(sw.split(".", 1)[0], "turn_on" if on else "turn_off", sw)

    async def _send_stop(self) -> None:
        mode = self.cfg[CONF_STOP_MODE]
        if mode == STOP_REPEAT and self._last_cmd:
            await self._switch((self._last_cmd == OPEN) != bool(self.cfg[CONF_INVERTED]))
        elif mode == STOP_ENTITY and self.cfg.get(CONF_STOP_ENTITY):
            ent = self.cfg[CONF_STOP_ENTITY]
            dom = ent.split(".", 1)[0]
            if dom == "button":
                await self._call("button", "press", ent)
            else:
                await self._call(dom, "turn_on", ent)

    async def _call(self, domain: str, service: str, entity_id: str) -> None:
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)
        except Exception as err:  # noqa: BLE001 - on ne veut jamais bloquer le volet
            _LOGGER.warning("%s : échec de %s.%s sur %s (%s)", self.cfg[CONF_NAME], domain, service, entity_id, err)

    # --------------------------------------------- calibrage (assistant UI)
    async def async_raw(self, cmd: str) -> None:
        """Commande brute, sans suivi de position (assistant de calibrage)."""
        self._cancel_timers()
        self.travel.stop(monotonic())
        await self._cmd(cmd)

    async def async_raw_stop(self) -> None:
        await self._send_stop()

    # ---------------------------------------------- interrupteurs / télécommandes
    @callback
    def _on_switch(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if (old is None) != (new is None) or (old and new and (old.state == STATE_UNAVAILABLE) != (new.state == STATE_UNAVAILABLE)):
            self.async_write_ha_state()

    @callback
    def _on_wall(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or old is None or old.state == STATE_UNAVAILABLE or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        etype = str(new.attributes.get("event_type", "")).lower()
        if etype in EV_OPEN:
            is_open = True
        elif etype in EV_CLOSE:
            is_open = False
        else:
            return
        if self.cfg[CONF_WALL_MODE] == WALL_DIRECT:
            # le moteur a déjà reçu l'ordre : on suit seulement
            if self.cfg[CONF_INVERTED]:
                is_open = not is_open
            self.hass.async_create_task(self._follow(OPEN if is_open else CLOSE), eager_start=True)
        else:
            self.hass.async_create_task(self._press(OPEN if is_open else CLOSE), eager_start=True)

    async def _press(self, cmd: str) -> None:
        direction = UP if cmd == OPEN else DOWN
        if self.travel.moving and self.travel.direction == direction:
            await self.async_stop_cover()
        else:
            await self._run(100 if cmd == OPEN else 0)

    async def _follow(self, cmd: str) -> None:
        direction = UP if cmd == OPEN else DOWN
        if self.travel.moving and self.travel.direction == direction:
            async with self._lock:
                self._last_cmd = cmd
                await self._stop(send=False)
        else:
            await self._run(100 if cmd == OPEN else 0, send=False)
