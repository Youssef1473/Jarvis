"""Base tool decorator and skill registry for Jarvis 2.0."""
import inspect
from typing import Callable, Dict, Any, List


class SkillRegistry:
    """Central registry holding all callable tools for Jarvis."""

    _tools: Dict[str, Callable] = {}
    _descriptions: Dict[str, str] = {}

    @classmethod
    def register(cls, name: str | None = None, description: str | None = None):
        """Decorator to register a Python function as a Jarvis skill tool."""
        def decorator(func: Callable):
            tool_name = name or func.__name__
            doc = description or (func.__doc__ or "").strip()
            cls._tools[tool_name] = func
            cls._descriptions[tool_name] = doc
            return func
        return decorator

    @classmethod
    def get_tool(cls, name: str) -> Callable | None:
        return cls._tools.get(name)

    @classmethod
    def get_all_tools(cls) -> Dict[str, Callable]:
        return cls._tools

    @classmethod
    def get_openai_tools(cls) -> List[Dict[str, Any]]:
        """Converts registered skills into OpenAI/xAI JSON schema tool definitions."""
        openai_tools = []
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for name, func in cls._tools.items():
            sig = inspect.signature(func)
            props = {}
            required = []

            for p_name, param in sig.parameters.items():
                if p_name in ("self", "cls"):
                    continue
                p_type = "string"
                if param.annotation != inspect.Parameter.empty:
                    p_type = type_map.get(param.annotation, "string")

                props[p_name] = {
                    "type": p_type,
                    "description": f"Parameter: {p_name}"
                }
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
        return openai_tools

    @classmethod
    def execute(cls, name: str, **kwargs) -> Any:
        tool = cls.get_tool(name)
        if not tool:
            return f"Error: Tool '{name}' not found."
        try:
            return tool(**kwargs)
        except Exception as e:
            return f"Error executing tool '{name}': {e}"
