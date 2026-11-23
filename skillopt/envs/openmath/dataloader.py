"""Data loader for normalized open-ended mathematics items."""
from __future__ import annotations

from skillopt.datasets.base import SplitDataLoader


class OpenMathDataLoader(SplitDataLoader):
    """Load the normalized ``items.json`` files produced by the materializer."""

