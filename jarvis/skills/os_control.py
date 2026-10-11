"""Operating system and desktop automation tools for Windows."""
import os
import subprocess
import pyautogui
from jarvis.skills.base import SkillRegistry


@SkillRegistry.register(
    name="launch_application",
    description="Launches a desktop application by name (e.g., 'notepad', 'calc', 'chrome', 'spotify', 'code')."
)
def launch_application(app_name: str) -> str:
    """Launches an application by name or executable."""
    app_lower = app_name.lower().strip()
    common_apps = {
        "calculator": "calc",
        "calc": "calc",
        "notepad": "notepad",
        "explorer": "explorer",
        "files": "explorer",
        "terminal": "wt",
        "cmd": "cmd",
        "powershell": "powershell",
        "code": "code",
        "vscode": "code",
        "chrome": "start chrome",
        "edge": "start msedge",
        "spotify": "start spotify:",
    }

    cmd = common_apps.get(app_lower, f"start {app_name}")
    try:
        subprocess.Popen(cmd, shell=True)
        return f"Application '{app_name}' launched, Sir."
    except Exception as e:
        return f"Failed to launch '{app_name}': {e}"


@SkillRegistry.register(
    name="adjust_volume",
    description="Adjusts system volume: 'up', 'down', or 'mute'."
)
def adjust_volume(action: str, steps: int = 5) -> str:
    """Adjusts Windows master volume using simulated key events."""
    action = action.lower().strip()
    if action == "up":
        for _ in range(steps):
            pyautogui.press("volumeup")
        return f"Volume increased by {steps * 2}%."
    elif action == "down":
        for _ in range(steps):
            pyautogui.press("volumedown")
        return f"Volume decreased by {steps * 2}%."
    elif action in ("mute", "unmute", "toggle"):
        pyautogui.press("volumemute")
        return "Volume mute toggled."
    return f"Unknown volume action '{action}'. Use 'up', 'down', or 'mute'."


@SkillRegistry.register(
    name="control_media",
    description="Controls media playback: 'playpause', 'next', 'previous'."
)
def control_media(action: str) -> str:
    """Controls Spotify / Windows media playback."""
    action = action.lower().strip()
    if action in ("play", "pause", "playpause", "toggle"):
        pyautogui.press("playpause")
        return "Media play/pause toggled."
    elif action in ("next", "skip"):
        pyautogui.press("nexttrack")
        return "Skipped to next track."
    elif action in ("previous", "prev", "back"):
        pyautogui.press("prevtrack")
        return "Returned to previous track."
    return f"Unknown media action '{action}'."


@SkillRegistry.register(
    name="minimize_all_windows",
    description="Minimizes all open windows to show the desktop."
)
def minimize_all_windows() -> str:
    """Shows the desktop."""
    pyautogui.hotkey("win", "d")
    return "Desktop shown, all windows minimized."
