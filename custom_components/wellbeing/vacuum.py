"""Vacuum platform for Wellbeing."""

import logging
from typing import Any

from homeassistant.components.vacuum import (
    Segment,
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.const import Platform

from . import WellbeingDataUpdateCoordinator
from .const import DOMAIN
from .entity import WellbeingEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)

SUPPORTED_FEATURES = (
    VacuumEntityFeature.START
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.FAN_SPEED
    | VacuumEntityFeature.SEND_COMMAND
    | VacuumEntityFeature.CLEAN_AREA
)

VACUUM_ACTIVITIES = {
    1: VacuumActivity.CLEANING,  # Regular Cleaning
    2: VacuumActivity.PAUSED,
    3: VacuumActivity.CLEANING,  # Stop cleaning
    4: VacuumActivity.PAUSED,  # Pause Spot cleaning
    5: VacuumActivity.RETURNING,
    6: VacuumActivity.PAUSED,  # Paused returning
    7: VacuumActivity.RETURNING,  # Returning for pitstop
    8: VacuumActivity.PAUSED,  # Paused returning for pitstop
    9: VacuumActivity.DOCKED,  # Charging
    10: VacuumActivity.IDLE,
    11: VacuumActivity.ERROR,
    12: VacuumActivity.DOCKED,  # Pitstop
    13: VacuumActivity.IDLE,  # Manual steering
    14: VacuumActivity.IDLE,  # Firmware upgrading
    "idle": VacuumActivity.IDLE,
    "inProgress": VacuumActivity.CLEANING,
    "goingHome": VacuumActivity.RETURNING,
    "paused": VacuumActivity.PAUSED,
    "sleeping": VacuumActivity.DOCKED,
    "vacuuming": VacuumActivity.CLEANING,
    "mopping": VacuumActivity.CLEANING,
    "vaccumAndMopping": VacuumActivity.CLEANING,
}

ROBOT_STATUS_LABELS_DE = {
    1: "Reinigt",
    2: "Pausiert",
    3: "Stoppt Reinigung",
    4: "Spot-Reinigung pausiert",
    5: "Fährt zur Ladestation",
    6: "Rückfahrt pausiert",
    7: "Pitstop (Fährt zum Zwischenladen)",
    8: "Pitstop-Rückfahrt pausiert",
    9: "In Ladestation (Geladen)",
    10: "Bereit (Schlummermodus)",
    11: "Fehler / Blockiert",
    12: "Pitstop (Lädt zwischen)",
    13: "Manuelle Steuerung",
    14: "Firmware-Update läuft",
}

DUSTBIN_LABELS_DE = {
    "connected": "Eingesetzt (OK)",
    "notconnected": "OK (Leer)",
    "full": "Voll – Bitte leeren!",
    "empty": "Leer (OK)",
}


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup vacuum platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    appliances = coordinator.data.get("appliances", None)

    if appliances is not None:
        for pnc_id, appliance in appliances.appliances.items():
            async_add_devices(
                [
                    WellbeingVacuum(
                        coordinator, entry, pnc_id, entity.entity_type, entity.attr
                    )
                    for entity in appliance.entities
                    if entity.entity_type == Platform.VACUUM
                ]
            )


class WellbeingVacuum(WellbeingEntity, StateVacuumEntity):
    """Wellbeing Vacuum class."""

    _attr_translation_key = "vacuum"

    def __init__(
        self,
        coordinator: WellbeingDataUpdateCoordinator,
        config_entry,
        pnc_id,
        entity_type,
        entity_attr,
    ):
        super().__init__(coordinator, config_entry, pnc_id, entity_type, entity_attr)
        self.entity_model = self.get_appliance.model

    @property
    def supported_features(self) -> int:
        return SUPPORTED_FEATURES

    @property
    def activity(self):
        """Return the current vacuum activity."""
        return VACUUM_ACTIVITIES.get(self.get_entity.state, VacuumActivity.ERROR)

    @property
    def fan_speed(self):
        """Return the current fan speed of the vacuum."""
        return self.get_appliance.vacuum_fan_speed

    @property
    def fan_speed_list(self):
        """Return the list of available fan speeds."""
        return self.get_appliance.vacuum_fan_speed_list

    @property
    def extra_state_attributes(self):
        """Return extended state attributes including map and zone metadata."""
        base = super().extra_state_attributes
        app = self.get_appliance
        raw_status = self.get_entity.state
        dustbin_raw = str(app.reported_state.get("dustbinStatus", "unknown"))
        selected_map = self.api.get_selected_map(self.pnc_id)
        all_maps = self.api.get_interactive_maps(self.pnc_id)

        active_zones = []
        if selected_map:
            active_zones = [
                z["name"]
                for z in selected_map.get("zones", [])
                if z.get("type") == "clean"
            ]

        all_zones = []
        for m in all_maps:
            for z in m.get("zones", []):
                if z.get("type") == "clean" and z["name"] not in all_zones:
                    all_zones.append(z["name"])

        return {
            **base,
            "status_code": raw_status,
            "status_detail": ROBOT_STATUS_LABELS_DE.get(raw_status, str(raw_status)),
            "dustbin_status": DUSTBIN_LABELS_DE.get(dustbin_raw.casefold(), dustbin_raw),
            "active_map": selected_map.get("name") if selected_map else None,
            "active_map_id": selected_map.get("id") if selected_map else None,
            "available_maps": [m["name"] for m in all_maps],
            "available_zones": active_zones,
            "all_zones": all_zones,
            "active_cleaning_zones": self.api.get_active_cleaning_zones(self.pnc_id),
        }

    async def async_start(self):
        """Start the vacuum cleaner."""
        await self.api.vacuum_start(self.pnc_id)
        self.coordinator.async_update_listeners()
        await self.async_refresh_later(5)

    async def async_stop(self):
        """Stop the vacuum cleaner."""
        await self.api.vacuum_stop(self.pnc_id)
        self.coordinator.async_update_listeners()
        await self.async_refresh_later(5)

    async def async_pause(self):
        """Pause the vacuum cleaner."""
        await self.api.vacuum_pause(self.pnc_id)
        self.coordinator.async_update_listeners()
        await self.async_refresh_later(5)

    async def async_return_to_base(self):
        """Return the vacuum cleaner to its base."""
        await self.api.vacuum_return_to_base(self.pnc_id)
        self.coordinator.async_update_listeners()
        await self.async_refresh_later(5)

    async def async_set_fan_speed(self, fan_speed: str) -> None:
        """Set the fan speed of the vacuum cleaner."""
        await self.api.vacuum_set_fan_speed(self.pnc_id, self.get_appliance, fan_speed)
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()

    async def async_get_segments(self) -> list[Segment]:
        """Get the segments that can be cleaned."""
        return await self.api.vacuum_get_segments(self.pnc_id)

    async def async_clean_segments(self, segment_ids: list[str], **kwargs: Any) -> None:
        """Perform an area clean."""
        await self.api.vacuum_clean_segments(self.pnc_id, segment_ids)
        self.coordinator.async_update_listeners()
        await self.async_refresh_later(5)

    async def async_send_command(
        self, command: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        """Send a custom command to the vacuum cleaner."""
        await self.api.vacuum_send_command(self.pnc_id, command, params)
        self.coordinator.async_update_listeners()
        await self.async_refresh_later(5)
