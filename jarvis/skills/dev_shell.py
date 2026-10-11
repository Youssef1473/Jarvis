"""Developer and shell execution tools for Windows."""
import os
import subprocess
from pathlib import Path
from jarvis.skills.base import SkillRegistry


@SkillRegistry.register(
    name="execute_powershell",
    description="Executes a PowerShell terminal command safely and returns its stdout and stderr."
)
def execute_powershell(command: str, timeout_seconds: int = 20) -> str:
    """Executes a PowerShell command with timeout."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        res = []
        if stdout:
            res.append(f"STDOUT:\n{stdout}")
        if stderr:
            res.append(f"STDERR:\n{stderr}")
        return "\n\n".join(res) if res else "Command completed with no output."
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout_seconds} seconds."
    except Exception as e:
        return f"Failed to execute command: {e}"


@SkillRegistry.register(
    name="list_directory",
    description="Lists files and subdirectories in a given folder path."
)
def list_directory(folder_path: str = ".") -> str:
    """Lists files in folder."""
    target = Path(folder_path).resolve()
    if not target.exists():
        return f"Directory does not exist: {target}"
    try:
        items = [f"{'[DIR] ' if p.is_dir() else '[FILE] '} {p.name}" for p in sorted(target.iterdir())]
        return "\n".join(items[:50]) or "Directory is empty."
    except Exception as e:
        return f"Error listing directory: {e}"


@SkillRegistry.register(
    name="read_file",
    description="Reads the text contents of a specified file."
)
def read_file(file_path: str, max_lines: int = 100) -> str:
    """Reads lines from a file."""
    target = Path(file_path).resolve()
    if not target.exists() or not target.is_file():
        return f"File does not exist: {target}"
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = [f.readline() for _ in range(max_lines)]
        return "".join(lines)
    except Exception as e:
        return f"Error reading file: {e}"
