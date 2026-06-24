"""Sensor platform for the SRNE BLE integration."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .protocol import NUM_CELLS

_V = UnitOfElectricPotential.VOLT
_A = UnitOfElectricCurrent.AMPERE
_C = UnitOfTemperature.CELSIUS


@dataclass(frozen=True, kw_only=True)
class SrneSensor(SensorEntityDescription):
    """A scalar telemetry field keyed by its decoded-dict key."""


SENSORS: tuple[SrneSensor, ...] = (
    SrneSensor(key="voltage", name="Voltage", native_unit_of_measurement=_V,
               device_class=SensorDeviceClass.VOLTAGE,
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2),
    SrneSensor(key="current", name="Current", native_unit_of_measurement=_A,
               device_class=SensorDeviceClass.CURRENT,
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2),
    SrneSensor(key="soc", name="State of Charge", native_unit_of_measurement=PERCENTAGE,
               device_class=SensorDeviceClass.BATTERY,
               state_class=SensorStateClass.MEASUREMENT),
    SrneSensor(key="soh", name="State of Health", native_unit_of_measurement=PERCENTAGE,
               state_class=SensorStateClass.MEASUREMENT),
    SrneSensor(key="remaining_capacity", name="Remaining Capacity",
               native_unit_of_measurement="Ah",
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1),
    SrneSensor(key="full_capacity", name="Full Charge Capacity",
               native_unit_of_measurement="Ah", suggested_display_precision=1),
    SrneSensor(key="rated_capacity", name="Rated Capacity",
               native_unit_of_measurement="Ah", suggested_display_precision=1),
    SrneSensor(key="cycles", name="Cycle Count",
               state_class=SensorStateClass.TOTAL_INCREASING),
    SrneSensor(key="dip_address", name="DIP Address",
               entity_category=EntityCategory.DIAGNOSTIC),
    SrneSensor(key="temp_1", name="Temperature 1", native_unit_of_measurement=_C,
               device_class=SensorDeviceClass.TEMPERATURE,
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1),
    SrneSensor(key="temp_2", name="Temperature 2", native_unit_of_measurement=_C,
               device_class=SensorDeviceClass.TEMPERATURE,
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1),
    SrneSensor(key="temp_3", name="Temperature 3", native_unit_of_measurement=_C,
               device_class=SensorDeviceClass.TEMPERATURE,
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1),
    SrneSensor(key="mos_temp", name="MOSFET Temperature", native_unit_of_measurement=_C,
               device_class=SensorDeviceClass.TEMPERATURE,
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1),
    SrneSensor(key="ambient_temp", name="Ambient Temperature", native_unit_of_measurement=_C,
               device_class=SensorDeviceClass.TEMPERATURE,
               state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up SRNE sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        SrneScalarSensor(coordinator, desc) for desc in SENSORS
    ]
    entities += [SrneCellSensor(coordinator, i) for i in range(NUM_CELLS)]
    async_add_entities(entities)


def _battery_label(coordinator) -> str:
    """Human pack label keyed off the DIP address (battery N = DIP N+1).

    Falls back to the BLE MAC when the DIP register has not been read yet.
    """
    dip = (coordinator.data or {}).get("dip_address")
    return f"{dip + 1}" if dip is not None else coordinator.address


def _device_info(coordinator) -> DeviceInfo:
    address = coordinator.address
    return DeviceInfo(
        connections={("bluetooth", address)},
        identifiers={(DOMAIN, address)},
        manufacturer="Tuner168 / SRNE",
        model="FP-Bat (LFP-B)",
        name=f"SRNE Battery {_battery_label(coordinator)}",
    )


class SrneScalarSensor(CoordinatorEntity, SensorEntity):
    """A single decoded telemetry field."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, description: SrneSensor) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._attr_device_info = _device_info(coordinator)

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.key)


class SrneCellSensor(CoordinatorEntity, SensorEntity):
    """One cell's voltage."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = _V
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator, index: int) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_name = f"Cell {index + 1} Voltage"
        self._attr_unique_id = f"{coordinator.address}_cell_{index + 1}"
        self._attr_device_info = _device_info(coordinator)

    @property
    def native_value(self):
        data = self.coordinator.data
        if not data:
            return None
        cells = data.get("cell_voltages") or []
        return cells[self._index] if self._index < len(cells) else None
