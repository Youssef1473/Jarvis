"""Semantic Windows UI Automation (UIA) tools for Jarvis 2.0.
Leverages pywinauto 0.6.9 to inspect accessibility trees, focus windows reliably,
and interact with native UI controls without blind synthetic keystrokes.
"""
import logging
import sys
import warnings
from typing import Dict, Any, List, Optional
from jarvis.skills.base import SkillRegistry

# Suppress benign pywinauto STA COM mode revert warning on Windows
warnings.filterwarnings("ignore", message=".*Revert to STA COM threading mode.*")

logger = logging.getLogger("Jarvis.WindowsUIA")


def is_uia_supported() -> bool:
    """Checks if UI Automation is supported on the current platform."""
    return sys.platform == "win32"


def focus_window_uia(title_query: str) -> Optional[str]:
    """Focuses a desktop window using pywinauto UI Automation backend.
    Bypasses standard Windows SetForegroundWindow lock by restoring and focusing the UIA element.
    """
    if not is_uia_supported() or not title_query:
        return None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pywinauto import Desktop
            d = Desktop(backend="uia")
        clean_target = title_query.strip().lower()

        # Find matching window elements
        windows = d.windows()
        for win in windows:
            try:
                text = win.window_text().strip()
                if text and clean_target in text.lower():
                    # Restore if minimized and bring to foreground
                    if win.is_minimized():
                        win.restore()
                    win.set_focus()
                    return text
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"pywinauto UIA focus fallback triggered: {e}")

    return None


@SkillRegistry.register(
    name="inspect_window_controls",
    description="Inspects native UI elements (buttons, inputs, menus, tabs) of an active window using Windows UI Automation (UIA) without taking screenshots."
)
def inspect_window_controls(window_title: str, max_depth: int = 2) -> str:
    """Inspects native accessibility tree controls for a given desktop window."""
    if not is_uia_supported():
        return "UI Automation is only supported on Windows workstations, Sir."

    target = (window_title or "").strip().lower()
    if not target:
        return "Window title cannot be empty, Sir."

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from pywinauto import Desktop
            d = Desktop(backend="uia")
        matched = None
        matched_title = ""

        for win in d.windows():
            try:
                t = win.window_text().strip()
                if t and target in t.lower():
                    matched = win
                    matched_title = t
                    break
            except Exception:
                continue

        if not matched:
            return f"Could not find an active window matching '{window_title}' to inspect, Sir."

        # Collect child controls
        controls = []
        try:
            children = matched.descendants(depth=max_depth)
            for child in children[:20]:
                c_type = child.element_info.control_type or "Unknown"
                c_name = child.element_info.name or ""
                c_auto_id = child.element_info.automation_id or ""
                label = f"• [{c_type}] {c_name}"
                if c_auto_id:
                    label += f" (id: {c_auto_id})"
                controls.append(label)
        except Exception as e:
            controls.append(f"• Traversal note: {e}")

        summary = "\n".join(controls) if controls else "No interactive child elements found."
        return (
            f"UI Automation Tree for '{matched_title}', Sir:\n"
            f"{summary}"
        )

    except Exception as e:
        logger.warning(f"Failed to inspect window controls via UIA: {e}")
        return f"Unable to inspect UI controls for '{window_title}': {e}"
