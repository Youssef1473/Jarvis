"""Chrome DevTools Protocol (CDP) client for sub-5ms silent browser control.
Supports Brave, Chrome, and Edge running with --remote-debugging-port=9222.
Enables instant tab listing, navigation, and opening without synthetic keystrokes or focus stealing.
"""
import json
import logging
import socket
import time
import urllib.parse
import urllib.request
from typing import Dict, Any, List, Optional, Tuple

from jarvis.skills.base import SkillRegistry

logger = logging.getLogger("Jarvis.BrowserCDP")

_CDP_PROBE_CACHE: Dict[Tuple[str, int], Tuple[bool, float]] = {}
_CDP_CACHE_TTL_SEC: float = 3.0


def is_cdp_available(host: str = "127.0.0.1", port: int = 9222, timeout_sec: float = 0.025) -> bool:
    """Non-blocking probe checking if Chrome DevTools Protocol port is listening on localhost."""
    now = time.time()
    cache_key = (host, port)
    if cache_key in _CDP_PROBE_CACHE:
        val, ts = _CDP_PROBE_CACHE[cache_key]
        if now - ts < _CDP_CACHE_TTL_SEC:
            return val

    # Restrict strictly to localhost for safety
    if host not in ("127.0.0.1", "localhost"):
        return False

    is_open = False
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            is_open = True
    except (OSError, socket.timeout):
        is_open = False

    _CDP_PROBE_CACHE[cache_key] = (is_open, now)
    return is_open


def list_cdp_tabs(host: str = "127.0.0.1", port: int = 9222) -> List[Dict[str, Any]]:
    """Retrieves all open page targets via CDP HTTP JSON interface."""
    if not is_cdp_available(host, port):
        return []

    url = f"http://{host}:{port}/json/list"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-CDP-Client"})
        with urllib.request.urlopen(req, timeout=0.15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [
                {
                    "id": item.get("id"),
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "ws_url": item.get("webSocketDebuggerUrl"),
                    "type": item.get("type", "page"),
                }
                for item in data
                if item.get("type") == "page"
            ]
    except Exception as e:
        logger.debug(f"Failed to query CDP tabs: {e}")
        return []


def open_or_navigate_tab_cdp(
    url: str,
    new_tab: bool = True,
    host: str = "127.0.0.1",
    port: int = 9222
) -> Optional[str]:
    """Navigates to URL via CDP. Reuses empty tabs or navigates an existing tab when new_tab is False.
    
    Returns:
        Status message if navigation succeeded via CDP, or None if CDP is unavailable / failed.
    """
    if not is_cdp_available(host, port):
        return None

    clean_url = url.strip()
    if clean_url and not clean_url.startswith(("http://", "https://", "about:", "chrome:", "brave:")):
        clean_url = "https://" + clean_url

    try:
        tabs = list_cdp_tabs(host, port)
        
        # 1. Look for existing blank / new tab page
        blank_tab = None
        for t in tabs:
            t_url = t.get("url", "").lower()
            t_title = t.get("title", "").lower()
            if any(k in t_url or k in t_title for k in ("about:blank", "chrome://newtab", "brave://newtab", "new tab")):
                blank_tab = t
                break

        target_to_reuse = None
        if not new_tab and tabs:
            target_to_reuse = tabs[0]
        elif blank_tab:
            target_to_reuse = blank_tab

        # If we have a tab to navigate, use WebSocket Page.navigate or HTTP activate
        if target_to_reuse and clean_url:
            ws_url = target_to_reuse.get("ws_url")
            if ws_url:
                try:
                    from websockets.sync.client import connect
                    with connect(ws_url, open_timeout=0.3, close_timeout=0.2) as ws:
                        cmd = {
                            "id": 1,
                            "method": "Page.navigate",
                            "params": {"url": clean_url}
                        }
                        ws.send(json.dumps(cmd))
                        # Read response with 0.3s timeout
                        ws.recv(timeout=0.3)
                    
                    # Also activate tab so user sees it
                    t_id = target_to_reuse.get("id")
                    if t_id:
                        activate_url = f"http://{host}:{port}/json/activate/{t_id}"
                        urllib.request.urlopen(activate_url, timeout=0.1)

                    return f"Navigated existing tab '{target_to_reuse.get('title') or 'New Tab'}' to '{clean_url}' via silent DevTools channel, Sir."
                except Exception as ws_err:
                    logger.debug(f"CDP WebSocket navigate failed, trying HTTP fallback: {ws_err}")

        # 2. Otherwise create a new tab via CDP HTTP PUT /json/new
        encoded = urllib.parse.quote(clean_url, safe=":/%?&=#") if clean_url else ""
        new_url = f"http://{host}:{port}/json/new?{encoded}" if encoded else f"http://{host}:{port}/json/new"
        req = urllib.request.Request(new_url, method="PUT")
        with urllib.request.urlopen(req, timeout=0.3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tab_id = data.get("id")
            if tab_id:
                # Activate new tab
                urllib.request.urlopen(f"http://{host}:{port}/json/activate/{tab_id}", timeout=0.1)
            return f"Opened '{clean_url or 'about:blank'}' via silent DevTools channel, Sir."

    except Exception as e:
        logger.warning(f"CDP navigation encountered error: {e}")
        return None


@SkillRegistry.register(
    name="list_open_browser_tabs",
    description="Lists all open tabs in Sir's browser via silent Chrome DevTools Protocol (CDP), including tab titles and URLs without stealing focus."
)
def list_open_browser_tabs() -> str:
    """Tool to inspect active browser tabs without foreground window disruption."""
    if not is_cdp_available():
        return "Browser DevTools Protocol is not currently attached (port 9222 is closed), Sir. Open tabs cannot be inspected silently without attaching debugging flags."

    tabs = list_cdp_tabs()
    if not tabs:
        return "No open browser tabs detected via DevTools channel, Sir."

    lines = ["Active Browser Tabs, Sir:"]
    for i, t in enumerate(tabs, 1):
        title = t.get("title", "Untitled")
        url = t.get("url", "")
        lines.append(f"{i}. {title} ({url})")

    return "\n".join(lines)
