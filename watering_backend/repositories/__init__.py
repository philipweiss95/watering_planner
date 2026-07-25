"""SQLite repositories grouped by aggregate ownership."""

from watering_backend.repositories.events import EventsRepository
from watering_backend.repositories.hoses import HosesRepository
from watering_backend.repositories.notifications import NotificationsRepository
from watering_backend.repositories.plants import PlantsRepository
from watering_backend.repositories.settings import SettingsRepository
from watering_backend.repositories.tanks import TanksRepository

__all__ = [
    "EventsRepository",
    "HosesRepository",
    "NotificationsRepository",
    "PlantsRepository",
    "SettingsRepository",
    "TanksRepository",
]
