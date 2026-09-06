"""Simule le chargement par Home Assistant pour attraper les erreurs d'import.

Une integration dont un module leve a l'import n'apparait pas du tout dans la
liste « Ajouter une integration », sans message visible dans l'interface. Ce
test rejoue ce chargement avec des doublures a la place de Home Assistant,
d'aiohttp et de voluptuous.

Il ne verifie pas le comportement — il verifie que chaque module se charge,
que chaque nom importe depuis homeassistant existe la ou on le cherche, et
qu'aucune syntaxe trop recente ne casse sur une version de Python plus
ancienne que celle du poste de developpement.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).parent.parent / "custom_components" / "discogs_valuation"

SUBMODULES = [
    "const",
    "valuation",
    "store",
    "fx",
    "api",
    "coordinator",
    "services",
    "config_flow",
    "sensor",
]


class _AnyMeta(type):
    """Rend les attributs de classe permissifs : Platform.SENSOR, etc."""

    def __getattr__(cls, name):
        return _Anything()

    def __getitem__(cls, item):
        # Absorbe DataUpdateCoordinator[dict], CoordinatorEntity[X], etc.
        return cls


class _Anything(metaclass=_AnyMeta):
    """Doublure permissive : attribut, appel, indexation, heritage, tout passe."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __init_subclass__(cls, **kwargs) -> None:
        # Absorbe les mots-cles de classe, par exemple ConfigFlow(domain=...).
        pass

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __getattr__(self, name):
        return _Anything()

    def __getitem__(self, item):
        return _Anything()

    def __iter__(self):
        return iter(())


def _cls(name: str) -> type:
    """Classe distincte par nom.

    Necessaire des que deux doublures servent de bases a la meme classe :
    `class X(CoordinatorEntity[C], SensorEntity)` leve « duplicate base class »
    si les deux pointent sur le meme objet.
    """
    return _AnyMeta(name, (_Anything,), {})


@dataclass(frozen=True, kw_only=True)
class _SensorEntityDescription:
    """Reproduit la vraie dataclass de Home Assistant.

    Les descriptions de capteurs heritent de cette classe et sont elles-memes
    decorees @dataclass : si la base n'est pas une dataclass, les champs
    herites (key, icon, state_class...) disparaissent du __init__ genere.
    """

    key: str
    translation_key: str | None = None
    name: object = None
    icon: str | None = None
    device_class: object = None
    state_class: object = None
    native_unit_of_measurement: str | None = None
    entity_category: object = None
    entity_registry_enabled_default: bool = True


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    mod.__getattr__ = lambda item: _Anything()  # type: ignore[attr-defined]
    return mod


def _stubs() -> dict[str, types.ModuleType]:
    return {
        "aiohttp": _module(
            "aiohttp",
            ClientSession=_cls("ClientSession"),
            ClientError=type("ClientError", (Exception,), {}),
            ClientTimeout=_cls("ClientTimeout"),
        ),
        "voluptuous": _module(
            "voluptuous",
            Schema=_cls("Schema"),
            Required=_cls("Required"),
            Optional=_cls("Optional"),
            All=_cls("All"),
            Range=_cls("Range"),
            In=_cls("In"),
        ),
        "homeassistant": _module("homeassistant"),
        "homeassistant.config_entries": _module(
            "homeassistant.config_entries",
            ConfigEntry=_cls("ConfigEntry"),
            ConfigFlow=_cls("ConfigFlow"),
            ConfigFlowResult=_cls("ConfigFlowResult"),
            OptionsFlow=_cls("OptionsFlow"),
        ),
        "homeassistant.const": _module("homeassistant.const", Platform=_Anything),
        "homeassistant.core": _module(
            "homeassistant.core",
            HomeAssistant=_cls("HomeAssistant"),
            ServiceCall=_cls("ServiceCall"),
            ServiceResponse=_cls("ServiceResponse"),
            SupportsResponse=_cls("SupportsResponse"),
            callback=lambda fn: fn,
        ),
        "homeassistant.helpers": _module("homeassistant.helpers"),
        "homeassistant.helpers.aiohttp_client": _module(
            "homeassistant.helpers.aiohttp_client",
            async_get_clientsession=_Anything,
        ),
        "homeassistant.helpers.device_registry": _module(
            "homeassistant.helpers.device_registry",
            DeviceEntryType=_cls("DeviceEntryType"),
            DeviceInfo=_cls("DeviceInfo"),
        ),
        "homeassistant.helpers.entity_platform": _module(
            "homeassistant.helpers.entity_platform",
            AddEntitiesCallback=_cls("AddEntitiesCallback"),
        ),
        "homeassistant.helpers.selector": _module(
            "homeassistant.helpers.selector",
            SelectSelector=_cls("SelectSelector"),
            SelectSelectorConfig=_cls("SelectSelectorConfig"),
            SelectSelectorMode=_cls("SelectSelectorMode"),
        ),
        "homeassistant.helpers.update_coordinator": _module(
            "homeassistant.helpers.update_coordinator",
            DataUpdateCoordinator=_cls("DataUpdateCoordinator"),
            CoordinatorEntity=_cls("CoordinatorEntity"),
            UpdateFailed=type("UpdateFailed", (Exception,), {}),
        ),
        "homeassistant.components": _module("homeassistant.components"),
        "homeassistant.components.sensor": _module(
            "homeassistant.components.sensor",
            SensorEntity=_cls("SensorEntity"),
            SensorEntityDescription=_SensorEntityDescription,
            SensorDeviceClass=_cls("SensorDeviceClass"),
            SensorStateClass=_cls("SensorStateClass"),
        ),
    }


@pytest.fixture
def loaded_package():
    """Charge le vrai package comme le ferait Home Assistant.

    Le conftest installe un package factice pour que les autres tests
    n'aient pas besoin de Home Assistant. Ici on veut au contraire executer
    le vrai __init__.py, donc on le remplace le temps du test.
    """
    saved = dict(sys.modules)
    for name in list(sys.modules):
        if name == "discogs_valuation" or name.startswith("discogs_valuation."):
            del sys.modules[name]
    sys.modules.update(_stubs())

    spec = importlib.util.spec_from_file_location(
        "discogs_valuation",
        PACKAGE_DIR / "__init__.py",
        submodule_search_locations=[str(PACKAGE_DIR)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["discogs_valuation"] = package
    spec.loader.exec_module(package)

    yield package

    sys.modules.clear()
    sys.modules.update(saved)


@pytest.mark.parametrize("name", [*SUBMODULES, "__init__"])
def test_module_parses(name: str):
    """Aucune syntaxe refusee par l'interpreteur courant.

    La syntaxe `type X = ...` (PEP 695) exige Python 3.12 : sur une instance
    Home Assistant en 3.11 elle provoque une SyntaxError et l'integration
    n'apparait jamais dans la liste, sans erreur visible dans l'interface.
    """
    ast.parse((PACKAGE_DIR / f"{name}.py").read_text(encoding="utf-8"))


def test_package_init_loads(loaded_package):
    """Le point d'entree se charge : c'est lui que Home Assistant importe."""
    assert hasattr(loaded_package, "async_setup_entry")
    assert hasattr(loaded_package, "async_unload_entry")


@pytest.mark.parametrize("name", SUBMODULES)
def test_submodule_imports(name: str, loaded_package):
    """Chaque module se charge, donc chaque nom importe existe bien."""
    importlib.import_module(f"discogs_valuation.{name}")


def test_no_pep695_type_statement():
    """Garde-fou explicite : cette syntaxe a deja casse le chargement une fois."""
    for path in PACKAGE_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            assert not isinstance(node, getattr(ast, "TypeAlias", ())), (
                f"{path.name} utilise `type X = ...`, incompatible Python 3.11"
            )


class TestManifest:
    """Sans manifeste correct, Home Assistant ignore le dossier en silence."""

    @pytest.fixture
    def manifest(self) -> dict:
        return json.loads((PACKAGE_DIR / "manifest.json").read_text(encoding="utf-8"))

    def test_config_flow_declared(self, manifest: dict):
        # Sans ca, l'integration n'est pas proposee dans « Ajouter une
        # integration » meme si elle se charge correctement.
        assert manifest["config_flow"] is True

    def test_folder_name_matches_domain(self, manifest: dict):
        assert PACKAGE_DIR.name == manifest["domain"]

    def test_required_keys(self, manifest: dict):
        for key in ("domain", "name", "version", "documentation", "codeowners"):
            assert key in manifest, key

    def test_translations_cover_config_flow(self):
        for lang in ("en", "fr"):
            path = PACKAGE_DIR / "translations" / f"{lang}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            assert "user" in data["config"]["step"], lang
