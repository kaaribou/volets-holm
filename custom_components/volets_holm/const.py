"""Constantes de l'intégration Volets HOLM."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "volets_holm"
NAME: Final = "Volets HOLM"
VERSION: Final = "1.0.0"

SUB_VOLET: Final = "volet"
SUB_LIAISON: Final = "liaison"

# --- volet temporisé -------------------------------------------------------
CONF_NAME: Final = "name"
CONF_SWITCH: Final = "switch"
CONF_INVERTED: Final = "inverted"
CONF_TIME_UP: Final = "time_up"
CONF_TIME_DOWN: Final = "time_down"
CONF_AREA: Final = "area_id"
CONF_WALL_EVENT: Final = "wall_event"
CONF_WALL_MODE: Final = "wall_mode"
CONF_STOP_MODE: Final = "stop_mode"
CONF_STOP_ENTITY: Final = "stop_entity"
CONF_STOP_AT_ENDS: Final = "send_stop_at_ends"
CONF_ENDSTOP_EXTRA: Final = "endstop_extra"
CONF_RECAL_FULL: Final = "recal_full"
CONF_LEGACY: Final = "legacy_entity_id"

WALL_RELAY: Final = "relay"      # l'interrupteur mural passe par Home Assistant
WALL_DIRECT: Final = "direct"    # l'interrupteur est appairé au moteur : on suit seulement

STOP_REPEAT: Final = "repeat"    # renvoyer la dernière commande (modules RF AC / DI-O)
STOP_ENTITY: Final = "entity"    # appeler un script / bouton / interrupteur dédié
STOP_NONE: Final = "none"

DEFAULT_TIME: Final = 20.0
DEFAULT_ENDSTOP_EXTRA: Final = 2.0

VOLET_DEFAULTS: Final = {
    CONF_INVERTED: False,
    CONF_TIME_UP: DEFAULT_TIME,
    CONF_TIME_DOWN: DEFAULT_TIME,
    CONF_WALL_MODE: WALL_RELAY,
    CONF_STOP_MODE: STOP_REPEAT,
    CONF_STOP_AT_ENDS: True,
    CONF_ENDSTOP_EXTRA: DEFAULT_ENDSTOP_EXTRA,
    CONF_RECAL_FULL: True,
}

# --- liaison interrupteur mural -> volet existant ---------------------------
CONF_EVENT: Final = "event"
CONF_COVER: Final = "cover"
CONF_STOP_WHEN_MOVING: Final = "stop_when_moving"

# types d'évènements reconnus sur les entités « event » (RFXCOM et autres)
EV_OPEN: Final = ("on", "group_on", "open", "up")
EV_CLOSE: Final = ("off", "group_off", "close", "down")

TICK_SECONDS: Final = 1.0
LEGACY_PLATFORM: Final = "cover_rf_time_based"
