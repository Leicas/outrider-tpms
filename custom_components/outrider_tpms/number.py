"""Number platform — user-tunable target pressure and tolerance.

These values feed the 'Pressure OK' binary sensor. They are persisted across
restarts via RestoreEntity and shared with other platforms through attributes
on the coordinator.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_TARGET_PSI,
    DEFAULT_TOLERANCE_PSI,
    DOMAIN,
    MAX_TARGET_PSI,
    MAX_TOLERANCE_PSI,
    MIN_TARGET_PSI,
    MIN_TOLERANCE_PSI,
)
from .coordinator import OutriderCoordinator


@dataclass(frozen=True, kw_only=True)
class OutriderNumberDescription(NumberEntityDescription):
    """Describe a tunable number stored as an attribute on the coordinator."""

    coordinator_attr: str
    default: float


NUMBERS: tuple[OutriderNumberDescription, ...] = (
    OutriderNumberDescription(
        key="target_pressure",
        translation_key="target_pressure",
        device_class=NumberDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PSI,
        native_min_value=MIN_TARGET_PSI,
        native_max_value=MAX_TARGET_PSI,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        coordinator_attr="target_psi",
        default=DEFAULT_TARGET_PSI,
    ),
    OutriderNumberDescription(
        key="pressure_tolerance",
        translation_key="pressure_tolerance",
        device_class=NumberDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PSI,
        native_min_value=MIN_TOLERANCE_PSI,
        native_max_value=MAX_TOLERANCE_PSI,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        coordinator_attr="tolerance_psi",
        default=DEFAULT_TOLERANCE_PSI,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: OutriderCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(OutriderNumber(coordinator, entry, desc) for desc in NUMBERS)


class OutriderNumber(
    CoordinatorEntity[OutriderCoordinator], NumberEntity, RestoreEntity
):
    """Persisted tunable that stores its value on the coordinator."""

    _attr_has_entity_name = True
    entity_description: OutriderNumberDescription

    def __init__(
        self,
        coordinator: OutriderCoordinator,
        entry: ConfigEntry,
        description: OutriderNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=entry.title,
            manufacturer="Outrider Components",
            model=f"TPMS ({coordinator.position})",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        value = self.entity_description.default
        if last_state is not None and last_state.state not in (
            None,
            "unknown",
            "unavailable",
        ):
            try:
                value = float(last_state.state)
            except (ValueError, TypeError):
                pass
        setattr(self.coordinator, self.entity_description.coordinator_attr, value)
        self.async_write_ha_state()
        # Make the binary sensor pick up the restored value immediately.
        self.coordinator.async_update_listeners()

    @property
    def native_value(self) -> float | None:
        return getattr(
            self.coordinator,
            self.entity_description.coordinator_attr,
            self.entity_description.default,
        )

    async def async_set_native_value(self, value: float) -> None:
        setattr(self.coordinator, self.entity_description.coordinator_attr, value)
        self.async_write_ha_state()
        self.coordinator.async_update_listeners()
