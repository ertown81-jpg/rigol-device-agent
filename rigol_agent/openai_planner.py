from __future__ import annotations

from .model_planner import CompatibleModelPlanner
from .tools import ToolRegistry


class OpenAIPlanner(CompatibleModelPlanner):
    """Backward-compatible OpenAI planner wrapper."""

    def __init__(self, tools: ToolRegistry, model: str | None = None) -> None:
        super().__init__(tools, provider="openai", model=model)
