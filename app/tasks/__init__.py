"""__init__.py for tasks package."""
from app.tasks.task_easy import EasyTask
from app.tasks.task_medium import MediumTask
from app.tasks.task_hard import HardTask

TASK_REGISTRY: dict = {
    "easy": EasyTask,
    "medium": MediumTask,
    "hard": HardTask,
}

__all__ = ["EasyTask", "MediumTask", "HardTask", "TASK_REGISTRY"]
