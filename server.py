"""Compatibility entry point for the modular watering planner backend."""

from __future__ import annotations

import sys

from watering_backend import core as _core


if __name__ == "__main__":
    _core.main()
else:
    # Existing integrations and tests import functions and patch runtime paths
    # on ``server``. During the compatibility period only the small facade is
    # aliased, so those assignments keep affecting its explicit adapters.
    # New code imports Application, repositories, services or API routes.
    sys.modules[__name__] = _core
