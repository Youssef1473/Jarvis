"""Operating system and desktop automation tools for Windows."""
import os
import sys
import re
import glob
import shutil
import logging
import winreg
import datetime
import subprocess
import threading
import time
import warnings
import webbrowser
from pathlib import Path

# Suppress benign pywinauto STA COM mode revert warning on Windows
warnings.filterwarnings("ignore", message=".*Revert to STA COM threading mode.*")
from typing import Dict, Any, Optional, List
import pyautogui
import psutil
import pyperclip
from jarvis.skills.base import SkillRegistry

logger = logging.getLogger("Jarvis.OSControl")

_app_index_cache: Dict[str, str] = {}
_app_index_initialized: bool = False


def _ensure_desktop_access():
    """Ensures thread is attached to the interactive Windows desktop."""
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    user32.SetProcessDPIAware()
                except Exception:
                    pass
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception as e:
            logger.debug(f"Desktop switch notice: {e}")


def _build_app_index() -> Dict[str, str]:
    """Scans and caches Windows Start Menu shortcuts (.lnk) and Registry App Paths."""
    global _app_index_cache, _app_index_initialized
    if _app_index_initialized:
        return _app_index_cache

    apps: Dict[str, str] = {}

    # 1. Windows App Paths registry (Direct .exe executable paths)
    for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root_key, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as key:
                num_subkeys, _, _ = winreg.QueryInfoKey(key)
                for i in range(num_subkeys):
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            app_path, _ = winreg.QueryValueEx(subkey, "")
                            if app_path and os.path.exists(app_path):
                                base_name = os.path.splitext(subkey_name)[0].lower()
                                apps[base_name] = app_path
                    except Exception:
                        continue
        except Exception:
            pass

    # 1b. Direct standard browser paths in LocalAppData / Program Files
    known_direct = {
        "brave": os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        "chrome": os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        "chrome_user": os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "msedge": os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    }
    for b_key, b_path in known_direct.items():
        if os.path.exists(b_path):
            clean_k = b_key.split("_")[0]
            if clean_k not in apps or not apps[clean_k].lower().endswith(".exe"):
                apps[clean_k] = b_path

    # 2. Start Menu shortcuts (.lnk files as secondary fallback)
    start_dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for s_dir in start_dirs:
        if not os.path.exists(s_dir):
            continue
        try:
            for root, _, files in os.walk(s_dir):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        name = os.path.splitext(f)[0].lower().strip()
                        full_path = os.path.join(root, f)
                        # Only register shortcut if not already indexed as an .exe
                        if name not in apps:
                            apps[name] = full_path
        except Exception as e:
            logger.debug(f"Start Menu scan notice for '{s_dir}': {e}")

    _app_index_cache = apps
    _app_index_initialized = True
    return _app_index_cache


COMMON_APP_ALIASES = {
    "browser": "browser",
    "my browser": "browser",
    "web browser": "browser",
    "default browser": "browser",
    "calculator": "calc",
    "calc": "calc",
    "notepad": "notepad",
    "text editor": "notepad",
    "explorer": "explorer",
    "files": "explorer",
    "file explorer": "explorer",
    "file manager": "explorer",
    "terminal": "wt",
    "windows terminal": "wt",
    "cmd": "cmd",
    "command prompt": "cmd",
    "powershell": "powershell",
    "code": "visual studio code",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "visual studio code": "visual studio code",
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "task manager": "taskmgr",
    "taskmgr": "taskmgr",
    "spotify": "spotify",
    "discord": "discord",
    "brave": "brave",
    "chrome": "chrome",
    "edge": "msedge",
    "control panel": "control",
    "paint": "mspaint",
}


@SkillRegistry.register(
    name="launch_application",
    description="Launches a desktop application by name (e.g., 'browser', 'chrome', 'discord', 'spotify', 'code', 'calculator', 'settings', 'notepad', 'terminal')."
)
def launch_application(app_name: str) -> str:
    """Launches an application by name, shortcut, or protocol scheme with detached process execution."""
    cleaned = re.sub(r"[;&|<>$`\"]", "", app_name).lower().strip()
    if not cleaned:
        return "Application name cannot be empty, Sir."

    # Handle browser requests naturally
    if cleaned in ("browser", "my browser", "web browser", "default browser"):
        apps = _build_app_index()
        # Prefer known installed browsers if present
        for b_name in ("brave", "chrome", "google chrome", "msedge", "edge", "firefox"):
            if b_name in apps:
                try:
                    os.startfile(apps[b_name])
                    return f"Opened your browser ({b_name.title()}), Sir."
                except Exception:
                    pass
        # Fallback to standard web browser open
        try:
            webbrowser.open("https://google.com")
            return "Opened your default desktop browser, Sir."
        except Exception as e:
            return f"Failed to open browser: {e}"

    # Resolve alias
    resolved = COMMON_APP_ALIASES.get(cleaned, cleaned)

    # 1. Direct protocol URI schemes
    if resolved.endswith(":") or resolved.startswith("ms-"):
        try:
            os.startfile(resolved)
            return f"Application '{app_name}' launched, Sir."
        except Exception as e:
            return f"Failed to launch '{app_name}': {e}"

    # 2. Check indexed shortcuts & app paths
    apps = _build_app_index()
    if resolved in apps:
        try:
            os.startfile(apps[resolved])
            return f"Application '{app_name}' launched, Sir."
        except Exception as e:
            return f"Failed to launch '{app_name}': {e}"

    # 2b. Substring match in indexed applications
    for name, path in apps.items():
        if resolved in name or name in resolved:
            try:
                os.startfile(path)
                return f"Application '{app_name}' ({name.title()}) launched, Sir."
            except Exception:
                continue

    # 3. Check System32 / PATH executables
    which_path = shutil.which(resolved)
    if which_path:
        try:
            subprocess.Popen(
                [which_path],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            return f"Application '{app_name}' launched, Sir."
        except Exception as e:
            return f"Failed to launch '{app_name}': {e}"

    # 4. Fallback: Windows start protocol
    try:
        os.startfile(resolved)
        return f"Application '{app_name}' launched, Sir."
    except Exception as e:
        return f"Could not find or launch application '{app_name}', Sir: {e}"


@SkillRegistry.register(
    name="launch_or_focus_application",
    description="Idempotent application activator: if the application or window is already running, brings it to focus via UIA; if closed, launches it."
)
def launch_or_focus_application(app_name: str) -> str:
    """Focuses open application or launches it if not currently running."""
    cleaned = (app_name or "").strip()
    if not cleaned:
        return "Application name cannot be empty, Sir."

    # First probe if window or process is already active
    try:
        from jarvis.skills.windows_uia import focus_window_uia
        focused_title = focus_window_uia(cleaned)
        if focused_title:
            return f"Switched to running application '{focused_title}', Sir."
    except Exception as e:
        logger.debug(f"UIA probe in launch_or_focus_application: {e}")

    # Fall back to Win32 window focus check
    try:
        from jarvis.skills.os_control import focus_window
        res = focus_window(cleaned)
        if "switched" in res.lower() or "focused" in res.lower():
            return res
    except Exception as e:
        logger.debug(f"focus_window check in launch_or_focus_application: {e}")

    # Not currently open -> launch detached process
    return launch_application(cleaned)


@SkillRegistry.register(
    name="search_desktop_files_fast",
    description="Fast file search prioritizing desktop and user folders (Documents, Downloads, Projects, Desktop) with depth-bounded traversal."
)
def search_desktop_files_fast(query: str, max_results: int = 10) -> str:
    """Performs fast file search prioritizing user directories and project trees."""
    q_clean = (query or "").lower().strip()
    if not q_clean:
        return "Please specify a file name or search term, Sir."

    user_home = Path.home()
    search_roots = [
        user_home / "Projects",
        user_home / "Desktop",
        user_home / "Documents",
        user_home / "Downloads",
    ]

    found = []
    for root in search_roots:
        if not root.exists():
            continue
        try:
            for p in root.rglob(f"*{q_clean}*"):
                if p.is_file():
                    parts = p.parts
                    if any(part.startswith(".") or part == "__pycache__" for part in parts):
                        continue
                    found.append(str(p))
                    if len(found) >= max_results:
                        break
        except Exception:
            continue
        if len(found) >= max_results:
            break

    if not found:
        return f"No files matching '{query}' were found across your primary directories, Sir."

    return f"Found {len(found)} file(s) matching '{query}', Sir:\n" + "\n".join(f"- {p}" for p in found)


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
        if sys.platform == "win32":
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass
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
    try:
        import win32api
        win32api.keybd_event(0x5B, 0, 0, 0)
        win32api.keybd_event(0x44, 0, 0, 0)
        win32api.keybd_event(0x44, 0, 2, 0)
        win32api.keybd_event(0x5B, 0, 2, 0)
    except Exception:
        import pyautogui
        pyautogui.FAILSAFE = False
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
    description="Shuts down Jarvis and its managed services when Sir says stop, goodbye, exit, quit, shut down, or that's it for now."
)
def shutdown_jarvis() -> str:
    """Terminates the Jarvis application."""
    global _shutdown_requested
    _shutdown_requested = True
    return "Powering down systems. Goodbye, Sir."


CRITICAL_PROCESS_DENYLIST = {
    "explorer.exe", "python.exe", "pythonw.exe", "svchost.exe", "csrss.exe",
    "winlogon.exe", "lsass.exe", "dwm.exe", "services.exe", "system", "smss.exe",
    "runtimebroker.exe", "sihost.exe", "taskhostw.exe", "audiodg.exe"
}


@SkillRegistry.register(
    name="close_application",
    description="Safely closes a running desktop application or window by name (e.g., 'notepad', 'calc', 'chrome', 'spotify', 'brave')."
)
def close_application(app_name: str) -> str:
    """Closes matching running desktop processes, enforcing critical system process protection."""
    cleaned = app_name.lower().strip()
    if not cleaned:
        return "Application name cannot be empty, Sir."

    # Normalize name and remove .exe if provided
    base_target = cleaned[:-4] if cleaned.endswith(".exe") else cleaned

    # Check for direct system critical protection
    if f"{base_target}.exe" in CRITICAL_PROCESS_DENYLIST or base_target in ("explorer", "python", "pythonw", "system"):
        return f"Cannot terminate system-critical process '{app_name}' for security and system stability reasons, Sir."

    closed_count = 0
    closed_names = []

    for proc in psutil.process_iter(['pid', 'name']):
        try:
            p_name = (proc.info['name'] or "").lower()
            if not p_name:
                continue

            # Skip any critical process
            if p_name in CRITICAL_PROCESS_DENYLIST:
                continue

            # Match target
            if base_target == p_name or f"{base_target}.exe" == p_name or (len(base_target) >= 4 and base_target in p_name):
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except psutil.TimeoutExpired:
                    proc.kill()
                closed_count += 1
                if proc.info['name'] not in closed_names:
                    closed_names.append(proc.info['name'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if closed_count > 0:
        names_str = ", ".join(closed_names)
        return f"Closed {closed_count} process(es) matching '{app_name}' ({names_str}), Sir."
    return f"No active process matching '{app_name}' was found to close, Sir."


@SkillRegistry.register(
    name="list_running_applications",
    description="Lists all visible desktop application windows currently active on Sir's workstation."
)
def list_running_applications() -> str:
    """Lists visible top-level desktop windows."""
    try:
        import win32gui
        _ensure_desktop_access()
        windows = []

        def _enum_win(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd).strip()
                if title and len(title) > 1 and title not in ("Default IME", "MSCTFIME UI", "Program Manager"):
                    windows.append(title)

        win32gui.EnumWindows(_enum_win, None)
        if not windows:
            return "No active desktop windows detected, Sir."

        unique = []
        seen = set()
        for w in windows:
            if w not in seen:
                seen.add(w)
                unique.append(w)

        return "Active Desktop Windows, Sir:\n" + "\n".join(f"• {w}" for w in unique[:15])
    except Exception as e:
        return f"Unable to list running applications: {e}"


@SkillRegistry.register(
    name="get_active_window",
    description="Returns the window title and process name of the application Sir is currently looking at or focused on."
)
def get_active_window() -> str:
    """Gets the title of the currently focused desktop window."""
    try:
        import win32gui
        import win32process
        _ensure_desktop_access()
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return "No window is currently in the foreground, Sir."
        title = win32gui.GetWindowText(hwnd).strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc_name = ""
        try:
            proc = psutil.Process(pid)
            proc_name = proc.name()
        except Exception:
            pass

        desc = f"'{title}'" if title else "Untitled Window"
        if proc_name:
            desc += f" ({proc_name})"
        return f"Currently focused window: {desc}, Sir."
    except Exception as e:
        return f"Could not determine active window: {e}"


@SkillRegistry.register(
    name="get_system_vitals",
    description="Reports real-time system performance telemetry: CPU utilization %, RAM usage (GB used/total), C: drive disk space, battery status, and GPU thermals/VRAM."
)
def get_system_vitals() -> str:
    """Reads non-blocking system hardware telemetry."""
    try:
        cpu_pct = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        mem_used_gb = mem.used / (1024 ** 3)
        mem_total_gb = mem.total / (1024 ** 3)
        disk = psutil.disk_usage("C:\\")
        disk_free_gb = disk.free / (1024 ** 3)

        vitals = [
            f"• CPU Utilization: {cpu_pct:.1f}%",
            f"• Memory (RAM): {mem_used_gb:.1f} GB of {mem_total_gb:.1f} GB utilized ({mem.percent}%)",
            f"• Primary Storage (C:): {disk_free_gb:.1f} GB free ({100 - disk.percent:.1f}% available)",
        ]

        batt = get_raw_battery_info()
        if batt.get("has_battery"):
            chg = "Plugged In" if batt.get("charging") else "On Battery"
            vitals.append(f"• Power / Battery: {batt.get('percent')}% ({chg})")

        # GPU telemetry via nvidia-smi if available
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().split(",")]
                if len(parts) >= 4:
                    gpu_name, vram_used, vram_total, temp = parts[0], parts[1], parts[2], parts[3]
                    vitals.append(f"• GPU: {gpu_name} at {temp}°C ({vram_used} MB / {vram_total} MB VRAM)")
        except Exception:
            pass

        return "Workstation System Vitals, Sir:\n" + "\n".join(vitals)
    except Exception as e:
        return f"Unable to read system vitals: {e}"


@SkillRegistry.register(
    name="read_clipboard",
    description="Reads and returns the current text stored in the Windows clipboard."
)
def read_clipboard() -> str:
    """Reads text from Windows clipboard."""
    try:
        text = pyperclip.paste()
        if not text or not text.strip():
            return "The clipboard is currently empty, Sir."
        preview = text[:500]
        suffix = f" ... [{len(text) - 500} more characters truncated]" if len(text) > 500 else ""
        return f"Clipboard Content ({len(text)} characters):\n{preview}{suffix}"
    except Exception as e:
        return f"Unable to read clipboard: {e}"


@SkillRegistry.register(
    name="copy_to_clipboard",
    description="Copies text directly to the Windows clipboard so Sir can paste it anywhere."
)
def copy_to_clipboard(text: str) -> str:
    """Copies text to Windows clipboard."""
    try:
        pyperclip.copy(text)
        return f"Successfully copied {len(text)} characters to the clipboard, Sir."
    except Exception as e:
        return f"Unable to copy to clipboard: {e}"


@SkillRegistry.register(
    name="lock_workstation",
    description="Immediately locks Sir's Windows workstation for security."
)
def lock_workstation() -> str:
    """Locks the Windows computer."""
    try:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return "Workstation locked, Sir."
    except Exception as e:
        return f"Unable to lock workstation: {e}"


@SkillRegistry.register(
    name="take_desktop_screenshot",
    description="Captures a high-resolution screenshot of the desktop display and saves it to data/screenshots."
)
def take_desktop_screenshot(filename: str = "") -> str:
    """Captures and saves a full-resolution desktop screenshot."""
    try:
        from PIL import ImageGrab
        _ensure_desktop_access()
        img = ImageGrab.grab(all_screens=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = filename.strip() or f"screenshot_{timestamp}.png"
        if not name.lower().endswith(".png"):
            name += ".png"

        save_dir = Path("data/screenshots")
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / name
        img.save(str(save_path), format="PNG")
        return f"Screenshot saved successfully to '{save_path}' ({img.width}x{img.height}), Sir."
    except Exception as e:
        return f"Failed to save desktop screenshot: {e}"


@SkillRegistry.register(
    name="open_browser_tab",
    description="Opens a new tab or navigates to a specific URL in Brave, Chrome, Edge, or the desktop browser. Reuses active empty tabs automatically without duplicate tabs."
)
def open_browser_tab(url: str = "", browser: str = "brave", new_tab: bool = True) -> str:
    """Opens a new tab or URL in the requested desktop browser.
    If the browser is already running on the desktop, focuses the active window and navigates.
    Reuses existing empty New Tab pages without redundant duplicate tabs.
    """
    browser_clean = (browser or "brave").lower().strip()
    target_url = url.strip()
    if target_url and not target_url.startswith(("http://", "https://", "about:", "chrome:", "brave:")):
        target_url = "https://" + target_url

    # 0. Sub-5ms Silent CDP Fast Path: If Chrome DevTools Protocol is available on port 9222
    if browser_clean in ("brave", "chrome", "edge", "browser"):
        try:
            from jarvis.skills.browser_cdp import open_or_navigate_tab_cdp
            cdp_res = open_or_navigate_tab_cdp(target_url, new_tab=new_tab)
            if cdp_res:
                return cdp_res
        except Exception as e:
            logger.debug(f"CDP fast path bypass: {e}")

    # 1. Fast Path: If an active browser window is already open, focus it and navigate
    try:
        import win32gui
        import win32con
        _ensure_desktop_access()
        matched_hwnds = []

        def _enum_win(hwnd, _):
            try:
                if win32gui.IsWindowVisible(hwnd):
                    w_title = win32gui.GetWindowText(hwnd).strip()
                    if w_title and browser_clean in w_title.lower():
                        matched_hwnds.append((hwnd, w_title))
            except Exception:
                pass
            return True

        win32gui.EnumWindows(_enum_win, None)

        if matched_hwnds:
            hwnd, found_title = matched_hwnds[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.06)
            pyautogui.FAILSAFE = False

            # Check if current tab is an empty New Tab / blank page
            is_blank_tab = any(
                k in found_title.lower()
                for k in ("new tab", "about:blank", "speed dial", "start page")
            )

            if target_url:
                # If sitting on a blank tab or new_tab is False, reuse the active tab directly via address bar!
                if not is_blank_tab and new_tab:
                    pyautogui.hotkey("ctrl", "t")
                    time.sleep(0.05)
                else:
                    pyautogui.hotkey("ctrl", "l")
                    time.sleep(0.04)

                old_clip = pyperclip.paste()
                pyperclip.copy(target_url)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.04)
                pyautogui.press("enter")

                def _restore():
                    time.sleep(0.3)
                    try:
                        pyperclip.copy(old_clip)
                    except Exception:
                        pass

                threading.Thread(target=_restore, daemon=True).start()
                return f"Opened '{target_url}' in your active {browser_clean.title()} window, Sir."
            else:
                # Blank tab requested
                if not is_blank_tab or new_tab:
                    pyautogui.hotkey("ctrl", "t")
                return f"Opened a new tab in your active {browser_clean.title()} window, Sir."
    except Exception as e:
        logger.debug(f"Active window tab notice: {e}")

    apps = _build_app_index()
    browser_exe = None
    for key, path in apps.items():
        if browser_clean in key or f"{browser_clean}.exe" in key:
            browser_exe = path
            break

    if not browser_exe:
        browser_exe = shutil.which(f"{browser_clean}.exe") or shutil.which(browser_clean)

    if not browser_exe:
        program_files = [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", "")
        ]
        candidates = {
            "brave": [
                r"BraveSoftware\Brave-Browser\Application\brave.exe",
                r"Programs\BraveSoftware\Brave-Browser\Application\brave.exe",
            ],
            "chrome": [
                r"Google\Chrome\Application\chrome.exe",
            ],
            "msedge": [
                r"Microsoft\Edge\Application\msedge.exe",
            ],
        }
        for base in program_files:
            if not base:
                continue
            for rel in candidates.get(browser_clean, []):
                cand = os.path.join(base, rel)
                if os.path.exists(cand):
                    browser_exe = cand
                    break
            if browser_exe:
                break

    if browser_exe:
        if browser_exe.lower().endswith(".lnk"):
            try:
                os.startfile(browser_exe)
                return f"Opened {browser_clean.title()} with a new tab, Sir."
            except Exception as e:
                logger.warning(f"Failed to start shortcut {browser_exe}: {e}")
        else:
            try:
                cmd = [browser_exe]
                if target_url:
                    cmd.append(target_url)
                else:
                    cmd.append("--new-tab")
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
                dest = f"to '{target_url}'" if target_url else "with a new tab"
                return f"Opened {browser_clean.title()} {dest}, Sir."
            except Exception as e:
                logger.warning(f"Failed to spawn {browser_clean}: {e}")

    try:
        webbrowser.open_new_tab(target_url or "https://google.com")
        return f"Opened a new browser tab {('to ' + target_url) if target_url else ''}, Sir."
    except Exception as e:
        return f"Failed to open browser tab: {e}"


@SkillRegistry.register(
    name="close_browser_tab",
    description="Terminates or closes an active or matching tab in Brave, Chrome, Edge, or the desktop browser. Prefers silent CDP if attached, falling back to foreground Win32 shortcut."
)
def close_browser_tab(browser: str = "brave", tab_title: str = "") -> str:
    """Terminates a tab in the requested browser.
    
    1. CDP fast path: Closes the matching or active tab silently over Chrome DevTools Protocol (port 9222).
    2. Win32 fallback: Focuses the browser window and presses Ctrl+W without stealing overall workflow context.
    """
    browser_clean = (browser or "brave").lower().strip()

    # 0. Sub-5ms Silent CDP Fast Path
    if browser_clean in ("brave", "chrome", "edge", "browser"):
        try:
            from jarvis.skills.browser_cdp import close_cdp_tab
            cdp_res = close_cdp_tab(title_or_url=tab_title)
            if cdp_res:
                return cdp_res
        except Exception as e:
            logger.debug(f"CDP close tab bypass: {e}")

    # 1. Desktop Window Fallback via UIA / Win32 + Ctrl+W
    try:
        # First attempt UIA semantic focus to bypass Windows SetForegroundWindow lock
        focused_via_uia = False
        try:
            from jarvis.skills.windows_uia import focus_window_uia
            focused_title = focus_window_uia(browser_clean)
            if focused_title:
                focused_via_uia = True
        except Exception as e:
            logger.debug(f"UIA focus bypass in close_browser_tab: {e}")

        if not focused_via_uia:
            import win32gui
            import win32con
            _ensure_desktop_access()
            matched_hwnds = []

            def _enum_win(hwnd, _):
                try:
                    if win32gui.IsWindowVisible(hwnd):
                        w_title = win32gui.GetWindowText(hwnd).strip()
                        if w_title and browser_clean in w_title.lower():
                            matched_hwnds.append((hwnd, w_title))
                except Exception:
                    pass
                return True

            win32gui.EnumWindows(_enum_win, None)

            if matched_hwnds:
                hwnd, found_title = matched_hwnds[0]
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    try:
                        win32gui.BringWindowToTop(hwnd)
                    except Exception:
                        pass
            else:
                return f"No open {browser_clean.title()} window was found to close a tab, Sir."

        time.sleep(0.06)
        pyautogui.FAILSAFE = False
        pyautogui.hotkey("ctrl", "w")
        time.sleep(0.04)
        return f"Closed active tab in your {browser_clean.title()} window, Sir."
    except Exception as e:
        return f"Failed to close browser tab: {e}"


@SkillRegistry.register(
    name="focus_window",
    description="Switches focus and brings an open desktop application or window to the foreground by name (e.g. 'brave', 'code', 'spotify', 'terminal')."
)
def focus_window(title: str) -> str:
    """Brings matching desktop window to the foreground."""
    target = (title or "").lower().strip()
    if not target:
        return "Window title or application name cannot be empty, Sir."

    # 0. UIA Semantic Focus Fast-Path: Bypasses Win32 lock via pywinauto
    try:
        from jarvis.skills.windows_uia import focus_window_uia
        uia_title = focus_window_uia(target)
        if uia_title:
            return f"Switched focus to '{uia_title}', Sir."
    except Exception as e:
        logger.debug(f"UIA focus bypass: {e}")

    try:
        import win32gui
        import win32con
        _ensure_desktop_access()

        matched_hwnds = []

        def _enum_win(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                w_title = win32gui.GetWindowText(hwnd).strip()
                if w_title and target in w_title.lower():
                    matched_hwnds.append((hwnd, w_title))

        win32gui.EnumWindows(_enum_win, None)

        if not matched_hwnds:
            return f"No open window matching '{title}' was found, Sir."

        hwnd, found_title = matched_hwnds[0]
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return f"Switched focus to '{found_title}', Sir."
    except Exception as e:
        return f"Failed to focus window '{title}': {e}"


@SkillRegistry.register(
    name="press_hotkey",
    description="Executes a keyboard shortcut or hotkey combination on Sir's PC (e.g. 'ctrl+t', 'ctrl+w', 'alt+tab', 'ctrl+c', 'ctrl+v', 'win+d', 'enter', 'escape')."
)
def press_hotkey(keys: str) -> str:
    """Safely presses a key combination via pyautogui."""
    raw = (keys or "").lower().strip()
    if not raw:
        return "Hotkey cannot be empty, Sir."

    if any(bad in raw for bad in ("ctrl+alt+del", "ctrl+alt+delete")):
        return "Execution of system-critical interrupt shortcut is prohibited, Sir."

    parts = [p.strip() for p in re.split(r"[+\s]+", raw) if p.strip()]
    if not parts:
        return f"Invalid hotkey format: '{keys}', Sir."

    try:
        _ensure_desktop_access()
        pyautogui.FAILSAFE = False
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
        return f"Executed hotkey '{'+'.join(parts)}', Sir."
    except Exception as e:
        return f"Failed to press hotkey '{keys}': {e}"


@SkillRegistry.register(
    name="type_text",
    description="Types text directly into the currently focused window or text input field on Sir's desktop."
)
def type_text(text: str) -> str:
    """Types text cleanly into the active window."""
    if not text:
        return "No text provided to type, Sir."
    try:
        _ensure_desktop_access()
        pyautogui.FAILSAFE = False
        old_clip = pyperclip.paste()
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.05)

        def _restore():
            time.sleep(0.3)
            try:
                pyperclip.copy(old_clip)
            except Exception:
                pass

        threading.Thread(target=_restore, daemon=True).start()
        return f"Typed {len(text)} characters into active window, Sir."
    except Exception as e:
        return f"Failed to type text: {e}"

