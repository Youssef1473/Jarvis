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


def _send_vk(vk_code: int):
    """Sends a native Win32 virtual keyboard event cleanly without mouse fail-safe interference."""
    try:
        import win32api
        win32api.keybd_event(vk_code, 0, 0, 0)
        win32api.keybd_event(vk_code, 0, 2, 0)
    except Exception:
        import pyautogui
        pyautogui.FAILSAFE = False
        if vk_code == 0xAF:
            pyautogui.press("volumeup")
        elif vk_code == 0xAE:
            pyautogui.press("volumedown")
        elif vk_code == 0xAD:
            pyautogui.press("volumemute")
        elif vk_code == 0xB3:
            pyautogui.press("playpause")
        elif vk_code == 0xB0:
            pyautogui.press("nexttrack")
        elif vk_code == 0xB1:
            pyautogui.press("prevtrack")


@SkillRegistry.register(
    name="adjust_volume",
    description="Adjusts system volume: 'up', 'down', 'mute', or 'set' to a target percentage (0-100)."
)
def adjust_volume(action: str, steps: int = 5, percent: int = -1) -> str:
    """Adjusts Windows master volume using native Win32 key events."""
    action = action.lower().strip()
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF

    if action == "set" or percent >= 0:
        target = max(0, min(100, percent if percent >= 0 else steps))
        # Zero out master volume, then step up (each volumeup step is 2%)
        for _ in range(50):
            _send_vk(VK_VOLUME_DOWN)
        up_steps = int(round(target / 2.0))
        for _ in range(up_steps):
            _send_vk(VK_VOLUME_UP)
        return f"Volume set to {target}%, Sir."
    elif action == "up":
        for _ in range(steps):
            _send_vk(VK_VOLUME_UP)
        return f"Volume increased by {steps * 2}%."
    elif action == "down":
        for _ in range(steps):
            _send_vk(VK_VOLUME_DOWN)
        return f"Volume decreased by {steps * 2}%."
    elif action in ("mute", "unmute", "toggle"):
        _send_vk(VK_VOLUME_MUTE)
        return "Volume mute toggled."
    return f"Unknown volume action '{action}'. Use 'up', 'down', 'mute', or 'set'."


@SkillRegistry.register(
    name="control_media",
    description="Controls media playback: 'playpause', 'next', 'previous'."
)
def control_media(action: str) -> str:
    """Controls Spotify / Windows media playback using native Win32 key events."""
    action = action.lower().strip()
    VK_MEDIA_NEXT = 0xB0
    VK_MEDIA_PREV = 0xB1
    VK_MEDIA_PLAY_PAUSE = 0xB3

    if action in ("play", "pause", "playpause", "toggle"):
        _send_vk(VK_MEDIA_PLAY_PAUSE)
        return "Media play/pause toggled."
    elif action in ("next", "skip"):
        _send_vk(VK_MEDIA_NEXT)
        return "Skipped to next track."
    elif action in ("previous", "prev", "back"):
        _send_vk(VK_MEDIA_PREV)
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


@SkillRegistry.register(
    name="get_battery_status",
    description="Gets the laptop battery charge percentage and AC power charging state."
)
def get_battery_status() -> str:
    """Reads hardware power status using native Windows Win32 API."""
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", wintypes.BYTE),
            ("BatteryFlag", wintypes.BYTE),
            ("BatteryLifePercent", wintypes.BYTE),
            ("SystemStatusFlag", wintypes.BYTE),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    sps = SYSTEM_POWER_STATUS()
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
        pct = sps.BatteryLifePercent
        is_charging = sps.ACLineStatus == 1
        charge_str = "plugged into AC power" if is_charging else "running on battery"
        if pct == 255:
            return "No battery detected (Desktop system)."
        return f"Battery is at {pct}%, currently {charge_str}."
    return "Unable to read battery status."


_shutdown_requested = False


def is_shutdown_requested() -> bool:
    global _shutdown_requested
    return _shutdown_requested


def set_shutdown_requested(value: bool = True):
    global _shutdown_requested
    _shutdown_requested = value


@SkillRegistry.register(
    name="shutdown_jarvis",
    description="Shuts down and terminates the Jarvis assistant completely when Sir says goodbye, exit, quit, shut down, or that's it for now."
)
def shutdown_jarvis() -> str:
    """Terminates the Jarvis application."""
    global _shutdown_requested
    _shutdown_requested = True
    return "Powering down systems. Goodbye, Sir."
