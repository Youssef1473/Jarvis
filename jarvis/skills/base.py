"""Base tool decorator and skill registry for Jarvis 2.0."""
import inspect
from typing import Callable, Dict, Any, Iterable, List


class SkillRegistry:
    """Central registry holding all callable tools for Jarvis."""

    _tools: Dict[str, Callable] = {}
    _descriptions: Dict[str, str] = {}
    _openai_tools_cache: Dict[tuple[str, ...], List[Dict[str, Any]]] = {}

    @classmethod
    def register(cls, name: str | None = None, description: str | None = None):
        """Decorator to register a Python function as a Jarvis skill tool."""
        def decorator(func: Callable):
            tool_name = name or func.__name__
            doc = description or (func.__doc__ or "").strip()
            cls._tools[tool_name] = func
            cls._descriptions[tool_name] = doc
            cls._openai_tools_cache.clear()
            return func
        return decorator

    @classmethod
    def get_tool(cls, name: str) -> Callable | None:
        return cls._tools.get(name)

    @classmethod
    def get_all_tools(cls) -> Dict[str, Callable]:
        return cls._tools

    @classmethod
    def unregister(cls, name: str) -> bool:
        """Unregisters a tool from the registry."""
        if name in cls._tools:
            del cls._tools[name]
            cls._descriptions.pop(name, None)
            cls._openai_tools_cache.clear()
            return True
        return False

    @classmethod
    def get_openai_tools(cls, names: Iterable[str] | None = None) -> List[Dict[str, Any]]:
        """Return cached OpenAI-compatible schemas for every tool or a selected subset."""
        selected = set(names) if names is not None else None
        ordered_names = tuple(name for name in cls._tools if selected is None or name in selected)
        cached = cls._openai_tools_cache.get(ordered_names)
        if cached is not None:
            return cached

        openai_tools = []
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for name in ordered_names:
            func = cls._tools[name]
            sig = inspect.signature(func)
            props = {}
            required = []

            for p_name, param in sig.parameters.items():
                if p_name in ("self", "cls"):
                    continue
                p_type = "string"
                if param.annotation != inspect.Parameter.empty:
                    p_type = type_map.get(param.annotation, "string")

                props[p_name] = {"type": p_type}
                if param.default == inspect.Parameter.empty:
                    required.append(p_name)

            desc = cls._descriptions.get(name) or (func.__doc__ or name).strip()
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required
                    }
                }
            })
        cls._openai_tools_cache[ordered_names] = openai_tools
        return openai_tools

    @classmethod
    def execute(cls, name: str, **kwargs) -> Any:
        aliases = {
            "store_fact": "remember_fact",
            "save_fact": "remember_fact",
            "get_fact": "recall_fact",
            "find_fact": "recall_fact",
            "remember": "remember_fact",
            "recall": "recall_fact",
        }
        resolved = aliases.get(name, name)
        tool = cls.get_tool(resolved)
        if not tool:
            return f"Error: Tool '{name}' not found."
        try:
            return tool(**kwargs)
        except Exception as e:
            return f"Error executing tool '{name}': {e}"
