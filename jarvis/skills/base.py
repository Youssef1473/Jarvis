"""Base tool decorator and skill registry for Jarvis 2.0."""
import inspect
from typing import Callable, Dict, Any, List


class SkillRegistry:
    """Central registry holding all callable tools for Jarvis."""

    _tools: Dict[str, Callable] = {}
    _declarations: List[Dict[str, Any]] = []

    @classmethod
    def register(cls, name: str | None = None, description: str | None = None):
        """Decorator to register a Python function as a Jarvis skill tool."""
        def decorator(func: Callable):
            tool_name = name or func.__name__
            doc = description or (func.__doc__ or "").strip()
            cls._tools[tool_name] = func
            return func
        return decorator

    @classmethod
    def get_tool(cls, name: str) -> Callable | None:
        return cls._tools.get(name)

    @classmethod
    def get_all_tools(cls) -> Dict[str, Callable]:
        return cls._tools

    @classmethod
    def execute(cls, name: str, **kwargs) -> Any:
        tool = cls.get_tool(name)
        if not tool:
            return f"Error: Tool '{name}' not found."
        try:
            return tool(**kwargs)
        except Exception as e:
            return f"Error executing tool '{name}': {e}"
