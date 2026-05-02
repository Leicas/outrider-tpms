"""Sensor platform for Outrider TPMS."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPressure
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OutriderCoordinator


@dataclass(frozen=True, kw_only=True)
class OutriderSensorDescription(SensorEntityDescription):
    """Describes one sensor derived from the coordinator's data dict."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[OutriderSensorDescription, ...] = (
    OutriderSensorDescription(
        key="pressure",
        translation_key="pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.PSI,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("gauge_psi"),
    ),
    OutriderSensorDescription(
        key="pressure_bar",
        translation_key="pressure_bar",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=2,
        value_fn=lambda d: d.get("gauge_bar"),
    ),
    OutriderSensorDescription(
        key="absolute_pressure",
        translation_key="absolute_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.PSI,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("absolute_psi"),
    ),
    OutriderSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("rssi"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Outrider sensors from a config entry."""
    coordinator: OutriderCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(OutriderSensor(coordinator, entry, desc) for desc in SENSORS)


class OutriderSensor(CoordinatorEntity[OutriderCoordinator], SensorEntity):
    """A single sensor reading backed by the coordinator."""

    _attr_has_entity_name = True
    entity_description: OutriderSensorDescription

    def __init__(
        self,
        coordinator: OutriderCoordinator,
        entry: ConfigEntry,
        description: OutriderSensorDescription,
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

    @property
    def available(self) -> bool:
        # The sensor pings only when the bike wakes (often once or twice a day),
        # so we keep the last value visible across the long disconnects rather
        # than going unavailable — otherwise the history graph is mostly gaps.
        if self.entity_description.key == "rssi":
            return self.coordinator.data is not None and self.coordinator.data.get("rssi") is not None
        return self.native_value is not None

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
