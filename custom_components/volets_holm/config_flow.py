"""Assistant de configuration Volets HOLM (tout se règle depuis l'interface)."""
from __future__ import annotations

from time import monotonic
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    AreaSelector,
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_AREA,
    CONF_COVER,
    CONF_ENDSTOP_EXTRA,
    CONF_EVENT,
    CONF_INVERTED,
    CONF_NAME,
    CONF_RECAL_FULL,
    CONF_STOP_AT_ENDS,
    CONF_STOP_ENTITY,
    CONF_STOP_MODE,
    CONF_STOP_WHEN_MOVING,
    CONF_SWITCH,
    CONF_TIME_DOWN,
    CONF_TIME_UP,
    CONF_WALL_EVENT,
    CONF_WALL_MODE,
    DOMAIN,
    NAME,
    STOP_ENTITY,
    STOP_NONE,
    STOP_REPEAT,
    SUB_LIAISON,
    SUB_VOLET,
    VOLET_DEFAULTS,
    WALL_DIRECT,
    WALL_RELAY,
)
from .migrate import async_find_legacy

ADVANCED = "avance"
ADVANCED_KEYS = (CONF_STOP_MODE, CONF_STOP_ENTITY, CONF_STOP_AT_ENDS, CONF_ENDSTOP_EXTRA, CONF_RECAL_FULL)


def _seconds(lo: float, hi: float, step: float = 0.5) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(min=lo, max=hi, step=step, unit_of_measurement="s", mode=NumberSelectorMode.BOX))


def _opt(key: str, value: Any) -> vol.Optional:
    return vol.Optional(key, description={"suggested_value": value}) if value not in (None, "") else vol.Optional(key)


def volet_schema(d: dict[str, Any]) -> vol.Schema:
    d = {**VOLET_DEFAULTS, **d}
    advanced = vol.Schema({
        vol.Required(CONF_STOP_MODE, default=d[CONF_STOP_MODE]): SelectSelector(SelectSelectorConfig(
            options=[STOP_REPEAT, STOP_ENTITY, STOP_NONE], translation_key="stop_mode", mode=SelectSelectorMode.DROPDOWN)),
        _opt(CONF_STOP_ENTITY, d.get(CONF_STOP_ENTITY)): EntitySelector(EntitySelectorConfig(domain=["script", "button", "switch", "scene"])),
        vol.Required(CONF_STOP_AT_ENDS, default=d[CONF_STOP_AT_ENDS]): BooleanSelector(),
        vol.Required(CONF_ENDSTOP_EXTRA, default=d[CONF_ENDSTOP_EXTRA]): _seconds(0, 30),
        vol.Required(CONF_RECAL_FULL, default=d[CONF_RECAL_FULL]): BooleanSelector(),
    })
    return vol.Schema({
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): TextSelector(),
        vol.Required(CONF_SWITCH, **({"default": d[CONF_SWITCH]} if d.get(CONF_SWITCH) else {})): EntitySelector(
            EntitySelectorConfig(domain=["switch", "light", "input_boolean"])),
        vol.Required(CONF_INVERTED, default=d[CONF_INVERTED]): BooleanSelector(),
        vol.Required(CONF_TIME_UP, default=d[CONF_TIME_UP]): _seconds(1, 300),
        vol.Required(CONF_TIME_DOWN, default=d[CONF_TIME_DOWN]): _seconds(1, 300),
        _opt(CONF_AREA, d.get(CONF_AREA)): AreaSelector(),
        _opt(CONF_WALL_EVENT, d.get(CONF_WALL_EVENT)): EntitySelector(EntitySelectorConfig(domain="event")),
        vol.Required(CONF_WALL_MODE, default=d[CONF_WALL_MODE]): SelectSelector(SelectSelectorConfig(
            options=[WALL_RELAY, WALL_DIRECT], translation_key="wall_mode", mode=SelectSelectorMode.LIST)),
        vol.Required(ADVANCED): section(advanced, {"collapsed": True}),
    })


def flatten(user_input: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {k: v for k, v in user_input.items() if k != ADVANCED}
    data.update(user_input.get(ADVANCED) or {})
    for k in (CONF_AREA, CONF_WALL_EVENT, CONF_STOP_ENTITY):
        if not data.get(k):
            data.pop(k, None)
    for k in (CONF_TIME_UP, CONF_TIME_DOWN, CONF_ENDSTOP_EXTRA):
        data[k] = float(data[k])
    if previous and previous.get("legacy_entity_id"):
        data["legacy_entity_id"] = previous["legacy_entity_id"]
    return data


def _check_volet(hass, data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not str(data.get(CONF_NAME, "")).strip():
        errors[CONF_NAME] = "name_required"
    if data.get(CONF_STOP_MODE) == STOP_ENTITY and not data.get(CONF_STOP_ENTITY):
        errors["base"] = "stop_entity_required"
    return errors


def liaison_schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        _opt(CONF_NAME, d.get(CONF_NAME)): TextSelector(),
        vol.Required(CONF_EVENT, **({"default": d[CONF_EVENT]} if d.get(CONF_EVENT) else {})): EntitySelector(EntitySelectorConfig(domain="event")),
        vol.Required(CONF_COVER, **({"default": d[CONF_COVER]} if d.get(CONF_COVER) else {})): EntitySelector(EntitySelectorConfig(domain="cover")),
        vol.Required(CONF_STOP_WHEN_MOVING, default=d.get(CONF_STOP_WHEN_MOVING, True)): BooleanSelector(),
        vol.Required(CONF_INVERTED, default=d.get(CONF_INVERTED, False)): BooleanSelector(),
    })


def liaison_title(hass, data: dict[str, Any]) -> str:
    if data.get(CONF_NAME):
        return data[CONF_NAME]
    ev, cv = hass.states.get(data[CONF_EVENT]), hass.states.get(data[CONF_COVER])
    return f"{ev.name if ev else data[CONF_EVENT]} → {cv.name if cv else data[CONF_COVER]}"


# =========================================================================== flux principal
class VoletsHolmConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._legacy: tuple[list[dict], list[dict], list[str]] = ([], [], [])

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUB_VOLET: VoletSubentryFlow, SUB_LIAISON: LiaisonSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is None:
            self._legacy = await async_find_legacy(self.hass)
        volets, liaisons, autos = self._legacy
        if user_input is not None:
            subs: list[ConfigSubentryData] = []
            if user_input.get("import_legacy"):
                subs += [ConfigSubentryData(subentry_type=SUB_VOLET, title=v[CONF_NAME], data=v, unique_id=None) for v in volets]
                subs += [ConfigSubentryData(subentry_type=SUB_LIAISON, title=l[CONF_NAME], data=l, unique_id=None) for l in liaisons]
                if user_input.get("disable_automations") and autos:
                    self.hass.async_create_task(
                        self.hass.services.async_call("automation", "turn_off", {"entity_id": autos, "stop_actions": False}),
                        eager_start=False,
                    )
            return self.async_create_entry(title=NAME, data={}, subentries=subs)

        schema: dict = {}
        if volets or liaisons:
            schema[vol.Required("import_legacy", default=True)] = BooleanSelector()
            if autos:
                schema[vol.Required("disable_automations", default=True)] = BooleanSelector()
        lines = [f"• {v[CONF_NAME]} ({v[CONF_TIME_UP]:g} s / {v[CONF_TIME_DOWN]:g} s)" for v in volets]
        lines += [f"• {l[CONF_NAME]}" for l in liaisons]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "volets": str(len(volets)),
                "liaisons": str(len(liaisons)),
                "automations": str(len(autos)),
                "details": "\n".join(lines) if lines else "—",
            },
        )


# =========================================================================== volet
class VoletSubentryFlow(ConfigSubentryFlow):
    def __init__(self) -> None:
        self._t0 = 0.0
        self._measured: dict[str, float] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = flatten(user_input)
            errors = _check_volet(self.hass, data)
            if not errors:
                return self.async_create_entry(title=data[CONF_NAME].strip(), data=data)
        return self.async_show_form(step_id="user", data_schema=volet_schema(flatten(user_input) if user_input else {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_show_menu(step_id="reconfigure", menu_options=["edit", "calib"])

    async def async_step_edit(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = flatten(user_input, dict(sub.data))
            errors = _check_volet(self.hass, data)
            if not errors:
                return self.async_update_and_abort(self._get_entry(), sub, title=data[CONF_NAME].strip(), data=data)
        return self.async_show_form(step_id="edit", data_schema=volet_schema(dict(sub.data)), errors=errors)

    # ------------------------------------------------ assistant de calibrage
    def _entity(self):
        entry = self._get_entry()
        hub = getattr(entry, "runtime_data", None)
        return hub.volets.get(self._get_reconfigure_subentry().subentry_id) if hub else None

    def _ends(self) -> bool:
        return bool({**VOLET_DEFAULTS, **self._get_reconfigure_subentry().data}[CONF_STOP_AT_ENDS])

    async def async_step_calib(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        ent = self._entity()
        if ent is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            await ent.async_raw("close")
            return await self.async_step_calib_closed()
        return self.async_show_form(step_id="calib", data_schema=vol.Schema({}))

    async def async_step_calib_closed(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            ent = self._entity()
            if self._ends():
                await ent.async_raw_stop()
            await ent.async_raw("open")
            self._t0 = monotonic()
            return await self.async_step_calib_up()
        return self.async_show_form(step_id="calib_closed", data_schema=vol.Schema({}))

    async def async_step_calib_up(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._measured[CONF_TIME_UP] = monotonic() - self._t0
            if self._ends():
                await self._entity().async_raw_stop()
            return await self.async_step_calib_top()
        return self.async_show_form(step_id="calib_up", data_schema=vol.Schema({}))

    async def async_step_calib_top(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            await self._entity().async_raw("close")
            self._t0 = monotonic()
            return await self.async_step_calib_down()
        return self.async_show_form(step_id="calib_top", data_schema=vol.Schema({}),
                                    description_placeholders={"up": f"{self._measured[CONF_TIME_UP]:.1f}"})

    async def async_step_calib_down(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._measured[CONF_TIME_DOWN] = monotonic() - self._t0
            if self._ends():
                await self._entity().async_raw_stop()
            return await self.async_step_calib_result()
        return self.async_show_form(step_id="calib_down", data_schema=vol.Schema({}))

    async def async_step_calib_result(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        if user_input is not None:
            data = {**sub.data, CONF_TIME_UP: float(user_input[CONF_TIME_UP]), CONF_TIME_DOWN: float(user_input[CONF_TIME_DOWN])}
            ent = self._entity()
            if ent is not None:
                await ent.async_set_known_position(0)
            return self.async_update_and_abort(self._get_entry(), sub, data=data)
        up = round(self._measured[CONF_TIME_UP] * 2) / 2
        down = round(self._measured[CONF_TIME_DOWN] * 2) / 2
        return self.async_show_form(
            step_id="calib_result",
            data_schema=vol.Schema({
                vol.Required(CONF_TIME_UP, default=max(up, 1.0)): _seconds(1, 300),
                vol.Required(CONF_TIME_DOWN, default=max(down, 1.0)): _seconds(1, 300),
            }),
            description_placeholders={
                "old_up": f"{sub.data.get(CONF_TIME_UP, '?')}",
                "old_down": f"{sub.data.get(CONF_TIME_DOWN, '?')}",
            },
        )


# =========================================================================== liaison
class LiaisonSubentryFlow(ConfigSubentryFlow):
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if v not in (None, "")}
            return self.async_create_entry(title=liaison_title(self.hass, data), data=data)
        return self.async_show_form(step_id="user", data_schema=liaison_schema({}))

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        sub = self._get_reconfigure_subentry()
        if user_input is not None:
            data = {k: v for k, v in user_input.items() if v not in (None, "")}
            return self.async_update_and_abort(self._get_entry(), sub, title=liaison_title(self.hass, data), data=data)
        return self.async_show_form(step_id="reconfigure", data_schema=liaison_schema(dict(sub.data)))
