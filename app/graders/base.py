"""
Abstract base class for all graders.
"""
from abc import ABC, abstractmethod
from typing import Any
from sqlalchemy.engine import Engine


class BaseGrader(ABC):
    @abstractmethod
    def score(self, engine: Engine, requirements: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """
        Returns (score: float in [0,1], details: dict with breakdown).
        """
        ...
