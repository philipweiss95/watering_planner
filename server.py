"""Compatibility entry point for the modular watering planner backend."""

from __future__ import annotations

import sys

from watering_backend import core as _core


if __name__ == "__main__":
    _core.main()
else:
    # Existing integrations and tests import functions and patch runtime paths
    # on ``server``. Expose the orchestration module itself during the migration
    # period so those assignments keep affecting function globals.
    sys.modules[__name__] = _core
