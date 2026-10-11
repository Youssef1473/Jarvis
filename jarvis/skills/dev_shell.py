"""Developer, codebase stewardship, and shell execution tools for Windows."""
import os
import sys
import re
import subprocess
from pathlib import Path
from typing import Optional
from jarvis.skills.base import SkillRegistry

WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent


def _resolve_workspace_path(file_path: str) -> Path:
    p = Path(file_path)
    if not p.is_absolute():
        p = WORKSPACE_DIR / p
    return p.resolve()


@SkillRegistry.register(
    name="execute_powershell",
    description="Executes a PowerShell terminal command safely inside the Jarvis workspace and returns stdout and stderr."
)
def execute_powershell(command: str, timeout_seconds: int = 25) -> str:
    """Executes a PowerShell command with timeout in the workspace directory."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=str(WORKSPACE_DIR),
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
    description="Lists files and subdirectories in a given folder path within the codebase."
)
def list_directory(folder_path: str = ".") -> str:
    """Lists files and folders with size in directory."""
    target = _resolve_workspace_path(folder_path)
    if not target.exists():
        return f"Directory does not exist: {target}"
    try:
        items = []
        for p in sorted(target.iterdir()):
            if p.name in (".git", "__pycache__", ".pytest_cache"):
                continue
            if p.is_dir():
                items.append(f"[DIR]  {p.name}/")
            else:
                size_kb = p.stat().st_size / 1024.0
                items.append(f"[FILE] {p.name:<28} ({size_kb:.1f} KB)")
        return "\n".join(items[:60]) or "Directory is empty."
    except Exception as e:
        return f"Error listing directory: {e}"


@SkillRegistry.register(
    name="read_file",
    description="Reads lines from a file with 1-indexed line numbers. Specify start_line and line_count to inspect specific code sections."
)
def read_file(file_path: str, start_line: int = 1, line_count: int = 100) -> str:
    """Reads lines from a file with line numbers."""
    target = _resolve_workspace_path(file_path)
    if not target.exists() or not target.is_file():
        return f"File does not exist: {target}"
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        total = len(all_lines)
        start_idx = max(1, start_line) - 1
        end_idx = min(total, start_idx + max(1, line_count))

        slice_lines = all_lines[start_idx:end_idx]
        formatted = [f"{start_idx + i + 1:>4}: {line.rstrip()}" for i, line in enumerate(slice_lines)]
        header = f"[{target.name} - Lines {start_idx + 1} to {end_idx} of {total}]"
        return header + "\n" + "\n".join(formatted)
    except Exception as e:
        return f"Error reading file: {e}"


@SkillRegistry.register(
    name="write_file",
    description="Creates a new file or completely overwrites an existing file in the workspace with new content."
)
def write_file(file_path: str, content: str, overwrite: bool = False) -> str:
    """Writes content to a file in the workspace."""
    target = _resolve_workspace_path(file_path)
    if target.exists() and not overwrite:
        return f"File already exists at '{target}'. Set overwrite=True if you intend to replace it."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        lines = content.splitlines()
        return f"Successfully wrote {len(content)} bytes ({len(lines)} lines) to {target.name}."
    except Exception as e:
        return f"Error writing file: {e}"


@SkillRegistry.register(
    name="patch_file",
    description="Surgically replaces a unique code snippet with new code inside an existing file."
)
def patch_file(file_path: str, target_snippet: str, replacement_snippet: str) -> str:
    """Replaces a unique target_snippet in a file with replacement_snippet."""
    target = _resolve_workspace_path(file_path)
    if not target.exists() or not target.is_file():
        return f"File does not exist: {target}"

    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            full_content = f.read()

        count = full_content.count(target_snippet)
        if count == 0:
            return f"Error: Target snippet was not found in '{target.name}'. Please ensure the snippet matches the existing file content exactly."
        if count > 1:
            return f"Error: Target snippet matched {count} times in '{target.name}'. Please include more surrounding lines to make the snippet unique."

        new_content = full_content.replace(target_snippet, replacement_snippet, 1)
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_content)

        return f"Successfully patched '{target.name}'. Replaced {len(target_snippet.splitlines())} lines with {len(replacement_snippet.splitlines())} lines."
    except Exception as e:
        return f"Error patching file: {e}"


@SkillRegistry.register(
    name="search_codebase",
    description="Recursively searches for text, symbols, or function names across codebase files."
)
def search_codebase(query: str, folder_path: str = ".", file_pattern: str = "*.py") -> str:
    """Searches for query in files matching file_pattern."""
    target_dir = _resolve_workspace_path(folder_path)
    if not target_dir.exists() or not target_dir.is_dir():
        return f"Directory does not exist: {target_dir}"

    results = []
    pattern_re = re.compile(re.escape(query), re.IGNORECASE)

    try:
        for p in target_dir.rglob(file_pattern):
            if any(part in p.parts for part in (".git", ".venv", "__pycache__", "data")):
                continue
            if not p.is_file():
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, 1):
                        if pattern_re.search(line):
                            rel_path = p.relative_to(WORKSPACE_DIR)
                            results.append(f"{rel_path}:{line_num}: {line.strip()[:100]}")
                            if len(results) >= 30:
                                break
            except Exception:
                continue
            if len(results) >= 30:
                break

        if not results:
            return f"No matches found for '{query}' in {file_pattern} files."
        return f"Found {len(results)} matches for '{query}':\n" + "\n".join(results)
    except Exception as e:
        return f"Error searching codebase: {e}"


@SkillRegistry.register(
    name="run_project_tests",
    description="Runs Python test suites in the tests/ directory to verify code changes."
)
def run_project_tests(test_file: str = "") -> str:
    """Executes tests using the current Python virtual environment."""
    py_exec = sys.executable
    target = f"tests/{test_file}".strip("/") if test_file else "tests"

    try:
        cmd = [py_exec, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"] if not test_file else [py_exec, f"tests/{test_file}"]
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (proc.stdout + "\n" + proc.stderr).strip()
        status = "PASSED" if proc.returncode == 0 else f"FAILED (Exit Code {proc.returncode})"
        return f"Test Run [{status}]:\n{out}"
    except subprocess.TimeoutExpired:
        return "Error: Test run timed out after 30 seconds."
    except Exception as e:
        return f"Error executing tests: {e}"


@SkillRegistry.register(
    name="git_status",
    description="Checks git repository status and current branch in the Jarvis workspace."
)
def git_status() -> str:
    """Returns git status and branch."""
    try:
        proc = subprocess.run(
            ["git", "status", "-s"],
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        branch_proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_proc.stdout.strip() or "unknown"
        changes = proc.stdout.strip() or "Working tree clean (no uncommitted changes)."
        return f"Branch: {branch}\n\nWorking Tree Status:\n{changes}"
    except Exception as e:
        return f"Git error: {e}"
