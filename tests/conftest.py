"""Permet de tester valuation.py et store.py sans installer Home Assistant.

Le __init__.py du package importe homeassistant. On enregistre donc un package
factice portant le bon __path__ : les sous-modules s'importent normalement,
sans que le vrai __init__.py ne soit execute. valuation.py et store.py n'ont
aucune dependance a HA, c'est deliberé et c'est ce qui rend le coeur testable.
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
