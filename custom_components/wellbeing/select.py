"""Select platform for Wellbeing."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.util import slugify

from .api import ApplianceSelect, Model
from .const import DEFAULT_NAME, DOMAIN
from .entity import WellbeingEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup select platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    appliances = coordinator.data.get("appliances", None)

    if appliances is not None:
        entities = []
        for pnc_id, appliance in appliances.appliances.items():
            entities.extend(
                [
                    WellbeingSelect(
                        coordinator, entry, pnc_id, entity.entity_type, entity.attr
                    )
                    for entity in appliance.entities
                    if entity.entity_type == Platform.SELECT
                    and appliance.has_capability(entity.attr)
                ]
            )
            if appliance.model == Model.PUREi9:
                entities.append(WellbeingMapSelect(coordinator, entry, pnc_id))
        async_add_devices(entities)


class WellbeingSelect(WellbeingEntity, SelectEntity):
    """Select for string properties such as LouverSwing or PMLeadingMode."""

    @property
    def options(self) -> list[str]:
        entity = self.get_entity
        defaults = entity.options if isinstance(entity, ApplianceSelect) else []
        options = self.get_appliance.capability_values(self.entity_attr, defaults)
        current = self.current_option
        if current is not None and current not in options:
            options = [*options, current]
        return options

    @property
    def current_option(self) -> str | None:
        state = self.get_entity.state
        return None if state is None else str(state)

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.api.set_property(self.pnc_id, self.entity_attr, option)
        self.set_optimistic_state(Platform.SELECT, self.entity_attr, option)
        self.set_optimistic_state(Platform.SENSOR, self.entity_attr, option)
        await self.async_refresh_later()


class WellbeingMapSelect(WellbeingEntity, SelectEntity):
    """Select entity to choose the active Interactive Map for PUREi9 / RX9 vacuums."""

    _attr_icon = "mdi:map-check-outline"

    def __init__(self, coordinator, config_entry, pnc_id):
        super().__init__(coordinator, config_entry, pnc_id, Platform.SELECT, "active_map")
        self.entity_id = f"select.{slugify(f'{DEFAULT_NAME}_{self.get_appliance.display_name}_active_map')}"

    @property
    def name(self) -> str:
        return "Aktive Karte"

    @property
    def unique_id(self) -> str:
        return f"{self.config_entry.entry_id}-activeMap-{self.pnc_id}"

    @property
    def device_class(self) -> str | None:
        return None

    @property
    def entity_category(self):
        return None

    @property
    def options(self) -> list[str]:
        names = self.api.get_map_names(self.pnc_id)
        return names if names else ["Standardkarte"]

    @property
    def current_option(self) -> str | None:
        selected = self.api.get_selected_map_name(self.pnc_id)
        opts = self.options
        if selected in opts:
            return selected
        return opts[0] if opts else None

    @property
    def extra_state_attributes(self):
        base = super().extra_state_attributes
        selected_map = self.api.get_selected_map(self.pnc_id)
        zones = []
        map_id = None
        if selected_map:
            map_id = selected_map.get("id")
            zones = [z["name"] for z in selected_map.get("zones", []) if z.get("type") == "clean"]
        return {
            **base,
            "map_id": map_id,
            "zones": zones,
            "zones_count": len(zones),
        }

    async def async_select_option(self, option: str) -> None:
        """Select active map for the vacuum."""
        self.api.set_selected_map_name(
            self.pnc_id, option, self.coordinator.data.get("appliances")
        )
        self.coordinator.async_update_listeners()
