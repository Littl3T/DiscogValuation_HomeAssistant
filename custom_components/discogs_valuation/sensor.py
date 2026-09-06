"""Sensors exposed to Home Assistant.

Three totals rather than a single figure: the market floor, the retained
(corrected) value and the raw value. Showing one number alone would imply a
precision the data does not have — on a real collection the gap between floor
and raw reaches a factor of 2.3.

Choice of state_class
---------------------
The monetary sensors use MEASUREMENT rather than TOTAL, and deliberately do not
declare device_class MONETARY.

MONETARY only accepts state_class TOTAL, which makes Home Assistant produce sum
statistics, with reset detection when the value drops. A collection's value
fluctuates both ways: a sum is meaningless here, and every dip would be read as
a counter reset.

MEASUREMENT produces min / mean / max per hour, which is exactly the series we
want for a value that rises and falls. We lose the interface's currency
formatting; we gain a correct long-term history, which is the whole point of
the project. The unit stays the currency code, filled in at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ValuationConfigEntry
from .const import DOMAIN
from .coordinator import ValuationCoordinator


@dataclass(frozen=True, kw_only=True)
class ValuationSensorDescription(SensorEntityDescription):
    """Description enriched with a value extractor."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    #: The unit is the snapshot's currency code, only known at runtime.
    monetary: bool = False


SENSORS: tuple[ValuationSensorDescription, ...] = (
    ValuationSensorDescription(
        key="total_value",
        translation_key="total_value",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:album",
        monetary=True,
        value_fn=lambda d: d.get("total_value"),
        attrs_fn=lambda d: {
            "raw_value": d.get("total_raw"),
            "market_floor": d.get("floor_total"),
            "capped_items": d.get("capped_count"),
            "low_confidence_items": d.get("low_confidence_count"),
            "source_currency": d.get("source_currency"),
            "fx_rate": d.get("fx_rate"),
        },
    ),
    ValuationSensorDescription(
        key="floor_total",
        translation_key="floor_total",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-arrow-down",
        monetary=True,
        value_fn=lambda d: d.get("floor_total"),
    ),
    ValuationSensorDescription(
        key="total_raw",
        translation_key="total_raw",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tag-arrow-up",
        monetary=True,
        value_fn=lambda d: d.get("total_raw"),
    ),
    ValuationSensorDescription(
        key="item_count",
        translation_key="item_count",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
        native_unit_of_measurement="records",
        value_fn=lambda d: d.get("item_count"),
        attrs_fn=lambda d: {
            "priced": d.get("priced_count"),
            "unpriced": d.get("unpriced_count"),
        },
    ),
    ValuationSensorDescription(
        key="avg_value",
        translation_key="avg_value",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:chart-bar",
        monetary=True,
        value_fn=lambda d: d.get("avg_value"),
    ),
    ValuationSensorDescription(
        key="delta_pct",
        translation_key="delta_pct",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        icon="mdi:trending-up",
        value_fn=lambda d: d.get("delta_pct"),
        attrs_fn=lambda d: {
            "absolute_change": d.get("delta_abs"),
            "item_change": d.get("delta_items"),
        },
    ),
    # Like-for-like: without it, buying a €40 record reads as market
    # appreciation.
    ValuationSensorDescription(
        key="like_for_like_pct",
        translation_key="like_for_like_pct",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        icon="mdi:scale-balance",
        value_fn=lambda d: (d.get("like_for_like") or {}).get("delta_pct"),
        attrs_fn=lambda d: {
            "common_items": (d.get("like_for_like") or {}).get("common_items"),
            "absolute_change": (d.get("like_for_like") or {}).get("delta_abs"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ValuationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        ValuationSensor(coordinator, entry, description) for description in SENSORS
    )


class ValuationSensor(CoordinatorEntity[ValuationCoordinator], SensorEntity):
    """One sensor backed by the coordinator."""

    _attr_has_entity_name = True
    entity_description: ValuationSensorDescription

    def __init__(
        self,
        coordinator: ValuationCoordinator,
        entry: ValuationConfigEntry,
        description: ValuationSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Discogs",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.monetary:
            return (self.coordinator.data or {}).get("currency")
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.coordinator.data or not self.entity_description.attrs_fn:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
