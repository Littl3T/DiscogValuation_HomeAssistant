"""Capteurs exposes a Home Assistant.

Trois totaux plutot qu'un chiffre unique : le plancher marche, la valeur
retenue (corrigee) et la valeur brute. Afficher un seul nombre donnerait une
fausse impression de precision — l'ecart entre plancher et brut atteint un
facteur 2,3 sur une collection reelle.

Choix de state_class
--------------------
Les capteurs monetaires utilisent MEASUREMENT et non TOTAL, et n'declarent
volontairement pas device_class MONETARY.

MONETARY n'admet que le state_class TOTAL, lequel fait produire a Home
Assistant des statistiques de type somme, avec detection de remise a zero
quand la valeur baisse. Or la valeur d'une collection fluctue dans les deux
sens : une somme n'a aucun sens ici, et chaque baisse serait interpretee comme
un reset de compteur.

MEASUREMENT produit min / moyenne / max par heure, ce qui est exactement la
serie voulue pour tracer une valeur qui monte et descend. On perd le formatage
monetaire de l'interface ; on gagne un historique long terme correct, qui est
tout l'objet du projet. L'unite reste le code devise, renseigne dynamiquement.
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
    """Description enrichie d'un extracteur de valeur."""

    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    #: L'unite est le code devise du snapshot, connu seulement a l'execution.
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
            "valeur_brute": d.get("total_raw"),
            "plancher_marche": d.get("floor_total"),
            "items_corriges": d.get("capped_count"),
            "items_faible_confiance": d.get("low_confidence_count"),
            "devise_source": d.get("source_currency"),
            "taux_de_change": d.get("fx_rate"),
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
        native_unit_of_measurement="disques",
        value_fn=lambda d: d.get("item_count"),
        attrs_fn=lambda d: {
            "valorises": d.get("priced_count"),
            "sans_prix": d.get("unpriced_count"),
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
            "variation_absolue": d.get("delta_abs"),
            "variation_nb_items": d.get("delta_items"),
        },
    ),
    # A perimetre constant : sans ca, acheter un disque a 40 EUR se lit comme
    # une appreciation du marche.
    ValuationSensorDescription(
        key="like_for_like_pct",
        translation_key="like_for_like_pct",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
        icon="mdi:scale-balance",
        value_fn=lambda d: (d.get("like_for_like") or {}).get("delta_pct"),
        attrs_fn=lambda d: {
            "items_communs": (d.get("like_for_like") or {}).get("common_items"),
            "variation_absolue": (d.get("like_for_like") or {}).get("delta_abs"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ValuationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Cree les capteurs."""
    coordinator = entry.runtime_data
    async_add_entities(
        ValuationSensor(coordinator, entry, description) for description in SENSORS
    )


class ValuationSensor(CoordinatorEntity[ValuationCoordinator], SensorEntity):
    """Un capteur adosse au coordinateur."""

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
