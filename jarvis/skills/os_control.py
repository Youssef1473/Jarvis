"""Operating system and desktop automation tools for Windows."""
import os
import re
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
    description="Adjusts Windows master volume to an exact percentage (0-100), increases/decreases, or toggles mute."
)
def adjust_volume(action: str = "set", percent: int = -1, steps: int = 5) -> str:
    """Adjusts Windows master volume with 100% precision using Core Audio API (pycaw)."""
    action_str = str(action or "").lower().strip()

    # Extract any numeric percentage from percent or action parameters
    target_pct = None
    if percent is not None:
        try:
            p_val = int(percent)
            if p_val >= 0:
                target_pct = p_val
        except (ValueError, TypeError):
            pass

    if target_pct is None:
        num_match = re.search(r'\b(\d{1,3})\b', action_str)
        if num_match:
            target_pct = int(num_match.group(1))

    # Core Audio endpoint control (pycaw) - sub-millisecond exact hardware precision
    try:
        from pycaw.pycaw import AudioUtilities
        spk = AudioUtilities.GetSpeakers()

        if target_pct is not None or "set" in action_str:
            target = max(0, min(100, target_pct if target_pct is not None else int(steps)))
            spk.volume_percent = float(target)
            return f"Master volume set to {target}%, Sir."

        elif any(k in action_str for k in ("up", "louder", "increase", "higher")):
            curr = float(spk.volume_percent or 50.0)
            target = max(0, min(100, int(round(curr + (int(steps) * 2)))))
            spk.volume_percent = float(target)
            return f"Volume increased to {target}%, Sir."

        elif any(k in action_str for k in ("down", "quieter", "decrease", "lower")):
            curr = float(spk.volume_percent or 50.0)
            target = max(0, min(100, int(round(curr - (int(steps) * 2)))))
            spk.volume_percent = float(target)
            return f"Volume decreased to {target}%, Sir."

        elif any(k in action_str for k in ("mute", "unmute", "toggle")):
            curr_mute = spk.EndpointVolume.GetMute()
            new_mute = 0 if curr_mute else 1
            spk.EndpointVolume.SetMute(new_mute, None)
            return "Volume unmuted, Sir." if curr_mute else "Volume muted, Sir."

    except Exception as e:
        logger.warning(f"pycaw volume adjustment error: {e}. Falling back to Win32 keybd_event.")

    # Win32 virtual key fallback if pycaw encounters an issue
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF

    if target_pct is not None or "set" in action_str:
        target = max(0, min(100, target_pct if target_pct is not None else 50))
        for _ in range(50):
            _send_vk(VK_VOLUME_DOWN)
        up_steps = int(round(target / 2.0))
        for _ in range(up_steps):
            _send_vk(VK_VOLUME_UP)
        return f"Volume set to {target}%, Sir."
    elif "up" in action_str:
        for _ in range(int(steps)):
            _send_vk(VK_VOLUME_UP)
        return "Volume increased."
    elif "down" in action_str:
        for _ in range(int(steps)):
            _send_vk(VK_VOLUME_DOWN)
        return "Volume decreased."
    elif "mute" in action_str:
        _send_vk(VK_VOLUME_MUTE)
        return "Volume mute toggled."

    return f"Unknown volume action '{action}'. Use 'set' (0-100), 'up', 'down', or 'mute'."


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


def get_raw_battery_info() -> dict:
    """Returns raw battery dictionary for UI telemetry."""
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
        return {
            "percent": 100 if pct == 255 else int(pct),
            "charging": bool(is_charging),
            "has_battery": pct != 255,
        }
    return {"percent": 100, "charging": True, "has_battery": False}


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
