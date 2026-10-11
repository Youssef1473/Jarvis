"""Safety gatekeeper for dangerous tool executions in Jarvis 2.0."""
import re
from typing import Dict, Any, Tuple


class SafetyGate:
    """Verifies whether commands or tools carry destructive risk."""

    DANGEROUS_COMMAND_PATTERNS = [
        r"\brm\b",
        r"\bdel\b",
        r"\brmdir\b",
        r"\bremove-item\b",
        r"\bformat\s+[a-zA-Z]:",
        r"\bshutdown\b",
        r"\brestart-computer\b",
        r"\bdrop\s+table\b",
        r"\bdelete\s+from\b",
        r"\bkill\b",
        r"\bstop-process\b",
    ]

    def __init__(self, config: Dict[str, Any]):
        self.confirm_destructive = config.get("safety", {}).get("confirm_destructive", True)

    def is_command_dangerous(self, command_str: str) -> bool:
        """Checks if a shell command contains potentially destructive verbs."""
        cmd_lower = command_str.lower()
        for pattern in self.DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, cmd_lower):
                return True
        return False

    def check_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Returns (is_safe, warning_message).
        If is_safe is False, Jarvis must ask for confirmation first.
        """
        if not self.confirm_destructive:
            return True, ""

        if tool_name == "execute_powershell":
            cmd = arguments.get("command", "")
            if self.is_command_dangerous(cmd):
                return False, f"The command '{cmd}' may modify or delete files. Are you certain you want me to proceed, Sir?"

        return True, ""
