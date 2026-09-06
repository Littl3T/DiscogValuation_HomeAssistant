"""Lets valuation.py and store.py be tested without installing Home Assistant.

The package's __init__.py imports homeassistant. So we register a stub package
carrying the right __path__: submodules import normally, without the real
__init__.py ever running. valuation.py and store.py have no dependency on HA,
which is deliberate and is what makes the core testable.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent / "custom_components" / "discogs_valuation"

if "discogs_valuation" not in sys.modules:
    stub = types.ModuleType("discogs_valuation")
    stub.__path__ = [str(PACKAGE_DIR)]
    sys.modules["discogs_valuation"] = stub
