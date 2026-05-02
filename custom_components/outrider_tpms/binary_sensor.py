"""Binary sensor — flags whether the latest gauge pressure is within
tolerance of the user-defined target. PROBLEM device class: 'on' = out of spec.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OutriderCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: OutriderCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OutriderPressureProblem(coordinator, entry)])


class OutriderPressureProblem(
    CoordinatorEntity[OutriderCoordinator], BinarySensorEntity
):
    _attr_has_entity_name = True
    _attr_translation_key = "pressure_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: OutriderCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_pressure_problem"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=entry.title,
            manufacturer="Outrider Components",
            model=f"TPMS ({coordinator.position})",
        )

    @property
    def available(self) -> bool:
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("gauge_psi") is not None

    @property
    def is_on(self) -> bool | None:
        gauge = self.coordinator.data.get("gauge_psi") if self.coordinator.data else None
        if gauge is None:
            return None
        return abs(gauge - self.coordinator.target_psi) > self.coordinator.tolerance_psi

    @property
    def extra_state_attributes(self) -> dict[str, float] | None:
        if not self.coordinator.data:
            return None
        gauge = self.coordinator.data.get("gauge_psi")
        if gauge is None:
            return None
        return {
            "target_psi": self.coordinator.target_psi,
            "tolerance_psi": self.coordinator.tolerance_psi,
            "deviation_psi": round(gauge - self.coordinator.target_psi, 2),
        }
