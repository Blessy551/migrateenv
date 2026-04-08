"""
Abstract base class for all MigrateEnv tasks.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class BaseTask(ABC):
    task_id: str = ""
    difficulty: str = ""  # "easy" | "medium" | "hard"
    description: str = ""
    target_description: str = ""
    max_steps: int = 10
    target_reward: float = 0.95

    @abstractmethod
    def get_initial_observation_data(self) -> dict[str, Any]:
        """
        Returns extra context added to the first Observation after reset.
        Example: {'focus_tables': ['customers'], 'note': '...' }
        """
        ...

    @abstractmethod
    def get_hint(self) -> str:
        """Returns a natural-language hint for the agent."""
        ...

    @abstractmethod
    def get_target_schema_requirements(self) -> dict[str, Any]:
        """
        Returns machine-readable requirements the grader uses.
        Structure is task-specific; grader knows how to interpret it.
        """
        ...

    def get_meta(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "difficulty": self.difficulty,
            "description": self.description,
            "target_description": self.target_description,
            "max_steps": self.max_steps,
            "target_reward": self.target_reward,
        }
