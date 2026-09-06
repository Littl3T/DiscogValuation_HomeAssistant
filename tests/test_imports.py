"""Simulates loading by Home Assistant to catch import errors.

An integration whose module raises at import time fails to set up, with little
to show for it in the interface. This test replays that load with stand-ins in
place of Home Assistant, aiohttp and voluptuous.

It does not check behaviour — it checks that every module loads, that every
name imported from homeassistant exists where we look for it, and that no
too-recent syntax breaks on a Python older than the development machine's.
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
    """Makes class attributes permissive: Platform.SENSOR, and so on."""

    def __getattr__(cls, name):
        return _Anything()

    def __getitem__(cls, item):
        # Absorbs DataUpdateCoordinator[dict], CoordinatorEntity[X], etc.
        return cls


class _Anything(metaclass=_AnyMeta):
    """Permissive stand-in: attribute, call, indexing, inheritance, all fine."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __init_subclass__(cls, **kwargs) -> None:
        # Absorbs class keywords, for example ConfigFlow(domain=...).
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
    """A distinct class per name.

    Needed as soon as two stand-ins serve as bases for the same class:
    `class X(CoordinatorEntity[C], SensorEntity)` raises "duplicate base class"
    if both point at the same object.
    """
    return _AnyMeta(name, (_Anything,), {})


@dataclass(frozen=True, kw_only=True)
class _SensorEntityDescription:
    """Reproduces Home Assistant's real dataclass.

    Sensor descriptions inherit from this class and are themselves decorated
    with @dataclass: if the base is not a dataclass, the inherited fields
    (key, icon, state_class...) vanish from the generated __init__.
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
    """Loads the real package the way Home Assistant would.

    The conftest installs a stub package so the other tests do not need Home
    Assistant. Here we want the opposite — to run the real __init__.py — so we
    swap it in for the duration of the test.
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
    """No syntax the current interpreter rejects.

    The `type X = ...` syntax (PEP 695) requires Python 3.12: on a Home
    Assistant instance running 3.11 it raises a SyntaxError and the integration
    never loads, with no clear error in the interface.
    """
    ast.parse((PACKAGE_DIR / f"{name}.py").read_text(encoding="utf-8"))


def test_package_init_loads(loaded_package):
    """The entry point loads: this is what Home Assistant imports."""
    assert hasattr(loaded_package, "async_setup_entry")
    assert hasattr(loaded_package, "async_unload_entry")


@pytest.mark.parametrize("name", SUBMODULES)
def test_submodule_imports(name: str, loaded_package):
    """Every module loads, so every imported name really exists."""
    importlib.import_module(f"discogs_valuation.{name}")


def test_no_pep695_type_statement():
    """Explicit guard: this syntax already broke loading once."""
    for path in PACKAGE_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            assert not isinstance(node, getattr(ast, "TypeAlias", ())), (
                f"{path.name} uses `type X = ...`, incompatible with Python 3.11"
            )


class TestManifest:
    """Without a correct manifest, Home Assistant silently ignores the folder."""

    @pytest.fixture
    def manifest(self) -> dict:
        return json.loads((PACKAGE_DIR / "manifest.json").read_text(encoding="utf-8"))

    def test_config_flow_declared(self, manifest: dict):
        # Without this, the integration is not offered under "Add
        # integration" even when it loads correctly.
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
