"""Liaisons « interrupteur mural -> volet existant » (Z-Wave, Zigbee, autre)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_COVER, CONF_EVENT, CONF_INVERTED, CONF_STOP_WHEN_MOVING, EV_CLOSE, EV_OPEN

_LOGGER = logging.getLogger(__name__)


class WallLink:
    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        self.hass = hass
        self.event = data[CONF_EVENT]
        self.cover = data[CONF_COVER]
        self.stop_when_moving = data.get(CONF_STOP_WHEN_MOVING, True)
        self.inverted = data.get(CONF_INVERTED, False)
        self._unsub: CALLBACK_TYPE | None = None

    @callback
    def start(self) -> None:
        self._unsub = async_track_state_change_event(self.hass, [self.event], self._on_event)

    @callback
    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _on_event(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or old is None or old.state == STATE_UNAVAILABLE or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        etype = str(new.attributes.get("event_type", "")).lower()
        if etype in EV_OPEN:
            want_open = True
        elif etype in EV_CLOSE:
            want_open = False
        else:
            return
        if self.inverted:
            want_open = not want_open
        cur = self.hass.states.get(self.cover)
        moving = cur.state if cur else None
        if self.stop_when_moving and moving == ("opening" if want_open else "closing"):
            service = "stop_cover"
        else:
            service = "open_cover" if want_open else "close_cover"
        self.hass.async_create_task(self._call(service), eager_start=True)

    async def _call(self, service: str) -> None:
        try:
            await self.hass.services.async_call("cover", service, {"entity_id": self.cover}, blocking=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Liaison %s -> %s : échec de cover.%s (%s)", self.event, self.cover, service, err)
