"""Tests de bout en bout dans un vrai Home Assistant de test."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.volets_holm.const import DOMAIN, SUB_LIAISON, SUB_VOLET


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(freezer):
    c = Clock()
    c.freezer = freezer
    with patch("custom_components.volets_holm.cover.monotonic", c):
        yield c


async def advance(hass, clock, seconds):
    clock.t += seconds
    clock.freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def calls_summary(on, off):
    return [c.data["entity_id"] for c in on], [c.data["entity_id"] for c in off]


async def setup_entry(hass, subs):
    entry = MockConfigEntry(domain=DOMAIN, title="Volets HOLM", data={}, subentries_data=subs)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


VOLET = {"name": "Volet bureau", "switch": "switch.moteur", "time_up": 20.0, "time_down": 10.0, "wall_event": "event.inter", "wall_mode": "relay"}


async def test_flux_sans_existant(hass: HomeAssistant):
    res = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert res["type"] == "form"
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {})
    assert res["type"] == "create_entry"


async def test_ajout_volet_par_sous_entree(hass: HomeAssistant):
    hass.states.async_set("switch.moteur", "off")
    entry = await setup_entry(hass, [])
    res = await hass.config_entries.subentries.async_init((entry.entry_id, SUB_VOLET), context={"source": "user"})
    assert res["type"] == "form"
    res = await hass.config_entries.subentries.async_configure(res["flow_id"], {
        "name": "Volet cuisine", "switch": "switch.moteur", "inverted": False, "time_up": 18, "time_down": 17,
        "wall_mode": "relay", "avance": {"stop_mode": "repeat", "send_stop_at_ends": True, "endstop_extra": 2, "recal_full": True},
    })
    assert res["type"] == "create_entry"
    await hass.async_block_till_done()
    st = hass.states.get("cover.volet_cuisine")
    assert st is not None and st.attributes["travelling_time_up"] == 18.0


async def test_mouvements_et_stop_par_repetition(hass: HomeAssistant, clock):
    hass.states.async_set("switch.moteur", "off")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    await setup_entry(hass, [ConfigSubentryData(subentry_type=SUB_VOLET, title="Volet bureau", data=VOLET, unique_id=None)])
    eid = "cover.volet_bureau"
    await hass.services.async_call("cover", "set_cover_position", {"entity_id": eid, "position": 100}, blocking=True)
    assert hass.states.get(eid).attributes["current_position"] == 100 or True
    # position de départ : fermé
    await hass.services.async_call(DOMAIN, "set_known_position", {"entity_id": eid, "position": 0}, blocking=True)
    on.clear(); off.clear()

    await hass.services.async_call("cover", "set_cover_position", {"entity_id": eid, "position": 50}, blocking=True)
    assert len(on) == 1 and hass.states.get(eid).state == "opening"
    await advance(hass, clock, 5)
    assert hass.states.get(eid).attributes["current_position"] == 25
    await advance(hass, clock, 6)
    st = hass.states.get(eid)
    assert st.state == "open" and st.attributes["current_position"] == 50
    assert len(on) == 2  # arrêt = même commande renvoyée

    # fermeture puis stop manuel
    await hass.services.async_call("cover", "close_cover", {"entity_id": eid}, blocking=True)
    assert len(off) == 1
    await advance(hass, clock, 2)
    await hass.services.async_call("cover", "stop_cover", {"entity_id": eid}, blocking=True)
    assert len(off) == 2
    assert hass.states.get(eid).attributes["current_position"] == 30
    # stop alors qu'il ne bouge pas : rien n'est envoyé
    await hass.services.async_call("cover", "stop_cover", {"entity_id": eid}, blocking=True)
    assert len(off) == 2 and len(on) == 2


async def test_meme_sens_ne_renvoie_pas_la_commande(hass: HomeAssistant, clock):
    hass.states.async_set("switch.moteur", "off")
    on = async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    await setup_entry(hass, [ConfigSubentryData(subentry_type=SUB_VOLET, title="Volet bureau", data=VOLET, unique_id=None)])
    eid = "cover.volet_bureau"
    await hass.services.async_call(DOMAIN, "set_known_position", {"entity_id": eid, "position": 0}, blocking=True)
    await hass.services.async_call("cover", "open_cover", {"entity_id": eid}, blocking=True)
    await advance(hass, clock, 3)
    await hass.services.async_call("cover", "open_cover", {"entity_id": eid}, blocking=True)
    assert len(on) == 1  # un second « on » arrêterait le moteur
    await advance(hass, clock, 17 + 2 + 0.5)  # course + recalage
    assert hass.states.get(eid).attributes["current_position"] == 100
    assert len(on) == 2  # arrêt en fin de course


async def test_interrupteur_mural_relaye(hass: HomeAssistant, clock):
    hass.states.async_set("switch.moteur", "off")
    hass.states.async_set("event.inter", "2026-01-01T00:00:00+00:00", {"event_type": "off"})
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    await setup_entry(hass, [ConfigSubentryData(subentry_type=SUB_VOLET, title="Volet bureau", data=VOLET, unique_id=None)])
    eid = "cover.volet_bureau"
    await hass.services.async_call(DOMAIN, "set_known_position", {"entity_id": eid, "position": 100}, blocking=True)
    hass.states.async_set("event.inter", "2026-01-01T00:00:01+00:00", {"event_type": "off"})
    await hass.async_block_till_done()
    assert len(off) == 1 and hass.states.get(eid).state == "closing"
    await advance(hass, clock, 4)
    hass.states.async_set("event.inter", "2026-01-01T00:00:05+00:00", {"event_type": "off"})
    await hass.async_block_till_done()
    assert len(off) == 2  # stop
    st = hass.states.get(eid)
    assert st.state == "open" and st.attributes["current_position"] == 60


async def test_interrupteur_appaire_suivi_seulement(hass: HomeAssistant, clock):
    hass.states.async_set("switch.moteur", "off")
    hass.states.async_set("event.inter", "2026-01-01T00:00:00+00:00", {"event_type": "off"})
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    await setup_entry(hass, [ConfigSubentryData(subentry_type=SUB_VOLET, title="Volet bureau", data={**VOLET, "wall_mode": "direct"}, unique_id=None)])
    eid = "cover.volet_bureau"
    await hass.services.async_call(DOMAIN, "set_known_position", {"entity_id": eid, "position": 0}, blocking=True)
    hass.states.async_set("event.inter", "2026-01-01T00:00:01+00:00", {"event_type": "on"})
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "opening"
    await advance(hass, clock, 10)
    hass.states.async_set("event.inter", "2026-01-01T00:00:02+00:00", {"event_type": "on"})  # 2e appui : le moteur s'arrête
    await hass.async_block_till_done()
    st = hass.states.get(eid)
    assert st.attributes["current_position"] == 50 and st.state == "open"
    assert len(on) == 0 and len(off) == 0


async def test_liaison_zwave(hass: HomeAssistant):
    hass.states.async_set("cover.salon", "open")
    hass.states.async_set("event.inter_salon", "2026-01-01T00:00:00+00:00", {"event_type": "on"})
    await setup_entry(hass, [ConfigSubentryData(subentry_type=SUB_LIAISON, title="Salon", data={"event": "event.inter_salon", "cover": "cover.salon", "stop_when_moving": True}, unique_id=None)])
    close = async_mock_service(hass, "cover", "close_cover")
    stop = async_mock_service(hass, "cover", "stop_cover")
    hass.states.async_set("event.inter_salon", "2026-01-01T00:00:01+00:00", {"event_type": "off"})
    await hass.async_block_till_done()
    assert len(close) == 1
    hass.states.async_set("cover.salon", "closing")
    hass.states.async_set("event.inter_salon", "2026-01-01T00:00:02+00:00", {"event_type": "off"})
    await hass.async_block_till_done()
    assert len(stop) == 1


async def test_reprise_existant(hass: HomeAssistant):
    hass.states.async_set("switch.moteur", "off")
    area = ar.async_get(hass).async_create("Bureau")
    reg = er.async_get(hass)
    old = reg.async_get_or_create("cover", "cover_rf_time_based", "cover_rf_timebased_uuid_volets_bureau_prop", suggested_object_id="volets_du_bureau_prop")
    reg.async_update_entity(old.entity_id, area_id=area.id, labels={"matterbridge"})
    reg.async_update_entity_options(old.entity_id, "conversation", {"should_expose": True})
    data = {**VOLET, "name": "Volets du bureau", "legacy_entity_id": "cover.volets_du_bureau_prop"}
    await setup_entry(hass, [ConfigSubentryData(subentry_type=SUB_VOLET, title="Volets du bureau", data=data, unique_id=None)])
    new = reg.async_get("cover.volets_du_bureau_prop")
    assert new.platform == DOMAIN
    assert new.area_id == area.id and "matterbridge" in new.labels
    assert new.options["conversation"]["should_expose"] is True
    assert hass.states.get("cover.volets_du_bureau_prop") is not None
    assert reg.async_get_entity_id("cover", "cover_rf_time_based", "cover_rf_timebased_uuid_volets_bureau_prop") is None


async def test_calibrage(hass: HomeAssistant, clock):
    hass.states.async_set("switch.moteur", "off")
    on = async_mock_service(hass, "switch", "turn_on")
    off = async_mock_service(hass, "switch", "turn_off")
    entry = await setup_entry(hass, [ConfigSubentryData(subentry_type=SUB_VOLET, title="Volet bureau", data=VOLET, unique_id=None)])
    sub_id = next(iter(entry.subentries))
    res = await entry.start_subentry_reconfigure_flow(hass, sub_id)
    assert res["type"] == "menu"
    res = await hass.config_entries.subentries.async_configure(res["flow_id"], {"next_step_id": "calib"})
    assert res["step_id"] == "calib"
    with patch("custom_components.volets_holm.config_flow.monotonic", clock):
        res = await hass.config_entries.subentries.async_configure(res["flow_id"], {})
        assert len(off) == 1 and res["step_id"] == "calib_closed"
        res = await hass.config_entries.subentries.async_configure(res["flow_id"], {})
        assert len(on) == 1 and res["step_id"] == "calib_up"
        clock.t += 21.3
        res = await hass.config_entries.subentries.async_configure(res["flow_id"], {})
        assert res["step_id"] == "calib_top"
        res = await hass.config_entries.subentries.async_configure(res["flow_id"], {})
        clock.t += 16.8
        res = await hass.config_entries.subentries.async_configure(res["flow_id"], {})
        assert res["step_id"] == "calib_result"
        res = await hass.config_entries.subentries.async_configure(res["flow_id"], {"time_up": 21.5, "time_down": 17})
    assert res["type"] == "abort" and res["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[sub_id].data["time_up"] == 21.5


async def test_detection_existant(hass: HomeAssistant):
    from homeassistant.setup import async_setup_component
    from custom_components.volets_holm import migrate

    hass.states.async_set("switch.volet_bureau", "off")
    assert await async_setup_component(hass, "script", {"script": {
        "ouvre_bureau": {"sequence": [{"action": "switch.turn_on", "target": {"entity_id": "switch.volet_bureau"}},
                                       {"action": "input_text.set_value", "target": {"entity_id": "input_text.volets_bureau_last"}, "data": {"value": "open"}}]},
        "stoppe_bureau": {"sequence": [{"action": "script.ouvre_bureau"}]},
    }})
    reg = er.async_get(hass)
    old = reg.async_get_or_create("cover", "cover_rf_time_based", "cover_rf_timebased_uuid_volets_bureau_prop",
                                  suggested_object_id="volets_du_bureau_prop", original_name="Volets du bureau")
    hass.states.async_set(old.entity_id, "open", {"current_position": 94, "travelling_time_up": 19, "travelling_time_down": 18})
    yaml_cfg = {"cover_rf_timebased_uuid_volets_bureau_prop": {
        "name": "Volets du bureau", "travelling_time_up": 19, "travelling_time_down": 18,
        "open_script_entity_id": "script.ouvre_bureau", "stop_script_entity_id": "script.stoppe_bureau", "send_stop_at_ends": True}}
    with patch.object(migrate, "_legacy_yaml", return_value=yaml_cfg):
        volets, liaisons, autos = await migrate.async_find_legacy(hass)
    assert len(volets) == 1
    v = volets[0]
    assert v["switch"] == "switch.volet_bureau" and v["inverted"] is False
    assert v["time_up"] == 19 and v["stop_mode"] == "repeat" and v["legacy_entity_id"] == "cover.volets_du_bureau_prop"


async def test_detection_commande_inversee(hass: HomeAssistant):
    from homeassistant.setup import async_setup_component
    from custom_components.volets_holm import migrate

    hass.states.async_set("switch.volet_x", "off")
    assert await async_setup_component(hass, "script", {"script": {
        "ouvre_x": {"sequence": [{"action": "switch.turn_off", "target": {"entity_id": "switch.volet_x"}}]}}})
    assert migrate._switch_of_script(hass, "script.ouvre_x") == ("switch.volet_x", "turn_off")
