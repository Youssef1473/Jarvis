"""Proactive ambient heartbeat daemon for Jarvis 2.0.
Monitors calendar appointments, task deadlines, battery status, unread emails,
and delivers autonomous morning standup briefings without requiring wake-words.
"""
import ctypes
from ctypes import wintypes
import datetime
import logging
import threading
import time
from typing import Dict, Any, Set, Optional

logger = logging.getLogger("Jarvis.Heartbeat")


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE),
        ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE),
        ("SystemStatusFlag", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class JarvisHeartbeat:
    """Ambient background monitor coordinating proactive spoken alerts, briefings, and desktop presence."""

    def __init__(self, orchestrator: Any, config: Dict[str, Any]):
        self.orchestrator = orchestrator
        self.config = config
        hb_cfg = config.get("heartbeat", {})

        self.enabled = hb_cfg.get("enabled", True)
        self.tick_interval = hb_cfg.get("tick_interval_seconds", 30)
        self.auto_briefing = hb_cfg.get("auto_morning_briefing", True)
        self.calendar_alerts = hb_cfg.get("calendar_alerts", True)
        self.battery_alerts = hb_cfg.get("battery_alerts", True)
        self.ergonomic_alerts = hb_cfg.get("ergonomic_reminders", True)
        self.email_alerts = hb_cfg.get("email_notifications", True)
        self.desktop_presence = hb_cfg.get("desktop_presence", True)
        self.idle_threshold_seconds = hb_cfg.get("idle_threshold_seconds", 300)

        # Presence & active window state tracking (Wake-Decide-Sleep)
        self._is_user_idle = False
        self._last_active_window = ""
        self._last_return_alert = 0.0

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Alert deduplication sets (persisted in memory to survive restarts)
        today_str = datetime.date.today().isoformat()
        try:
            stored_alerted = self.orchestrator.memory.get_fact(f"alerted_events_{today_str}") or ""
            self._alerted_event_ids: Set[int] = {int(x) for x in stored_alerted.split(",") if x.strip().isdigit()}
        except Exception:
            self._alerted_event_ids: Set[int] = set()
        self._alerted_task_ids: Set[int] = set()

        # Cooldown timestamps
        self._last_battery_alert = 0.0
        self._last_break_reminder = time.time()
        self._last_email_check = 0.0
        self._last_unread_email_count: Optional[int] = None

        # Session metrics
        self._session_start_time = time.time()
        # Startup grace period (wait 15s after startup before proactive alerts)
        self._startup_grace_period = time.time() + 15.0

        # Memory key for daily briefing tracking
        today_str = datetime.date.today().isoformat()
        last_briefing_date = self.orchestrator.memory.get_fact("last_briefing_date")
        self._briefing_delivered_today = (last_briefing_date == today_str)

    def start(self):
        """Starts the ambient heartbeat thread."""
        if not self.enabled:
            logger.info("Heartbeat daemon disabled in configuration.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._heartbeat_loop, name="JarvisHeartbeatDaemon", daemon=True)
        self._thread.start()
        logger.info("Jarvis Proactive Heartbeat daemon started.")

    def stop(self):
        """Stops the ambient heartbeat loop."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("Jarvis Proactive Heartbeat daemon stopped.")

    def speak_proactive(self, text: str) -> bool:
        """
        Politely delivers a proactive spoken announcement.
        Bypasses or postpones if user is currently speaking or in push-to-talk.
        """
        if not text or not text.strip():
            return False

        # Guard: Never talk over the user
        if getattr(self.orchestrator, "_is_recording", False) or self.orchestrator.tts.is_speaking:
            logger.info("Heartbeat alert postponed: audio channel is busy.")
            return False

        logger.info(f"Proactive Announcement: {text}")
        print(f"\n[Jarvis Proactive]: {text}\n")

        try:
            self.orchestrator.hud.set_state("speaking")
            self.orchestrator.sfx.play_alert()
            time.sleep(0.35)

            # Start streaming speech
            self.orchestrator.tts.start_streaming_session()
            import re
            sentences = [s.strip() for s in re.split(r"(?<!\d)(?<=[.!?])(?!\d)\s+", text) if s.strip()]
            for s in sentences:
                self.orchestrator.tts.queue_sentence(s)
            self.orchestrator.tts.finish_streaming_session()

            self.orchestrator.sfx.play_done()
            self.orchestrator.hud.set_state("idle")
            return True
        except Exception as e:
            logger.error(f"Error in proactive speech delivery: {e}")
            self.orchestrator.hud.set_state("idle")
            return False

    def _heartbeat_loop(self):
        """Main periodic evaluation loop."""
        # Initial boot delay
        time.sleep(5.0)

        while self._running:
            try:
                now = time.time()
                if now >= self._startup_grace_period:
                    if self.desktop_presence:
                        self._check_desktop_presence()
                    if self.auto_briefing:
                        self._check_morning_briefing()
                    if self.calendar_alerts:
                        self._check_calendar_events()
                    if self.battery_alerts:
                        self._check_battery_status()
                    if self.ergonomic_alerts:
                        self._check_ergonomics()
                    if self.email_alerts:
                        self._check_new_emails()
            except Exception as e:
                logger.error(f"Heartbeat tick exception: {e}")

            # Laptop Thermal Profile: relax timer frequency on battery power to prevent waking CPU cores
            sleep_duration = self.tick_interval * 2 if self._is_on_battery() else self.tick_interval
            time.sleep(sleep_duration)

    def get_idle_seconds(self) -> float:
        """Returns the number of seconds since the last physical keyboard or mouse input (<0.01ms)."""
        try:
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return max(0.0, float(millis) / 1000.0)
        except Exception as e:
            logger.debug(f"Win32 GetLastInputInfo notice: {e}")
        return 0.0

    def get_active_window_title(self) -> str:
        """Returns the window title of the current foreground window in <0.05ms."""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    return buf.value.strip()
        except Exception as e:
            logger.debug(f"Win32 GetForegroundWindow notice: {e}")
        return ""

    def _check_desktop_presence(self):
        """Monitors user presence and active workstation context (Wake-Decide-Sleep architecture)."""
        idle_sec = self.get_idle_seconds()
        current_title = self.get_active_window_title()

        # 1. User Idle / Absence State Transition
        if idle_sec >= self.idle_threshold_seconds:
            if not self._is_user_idle:
                self._is_user_idle = True
                logger.info(f"Workstation user presence transitioned to IDLE ({idle_sec:.0f}s elapsed).")
                if hasattr(self.orchestrator, "broadcast_ambient_telemetry"):
                    self.orchestrator.broadcast_ambient_telemetry({
                        "event": "user_idle",
                        "idle_duration_s": round(idle_sec, 1),
                    })
        elif self._is_user_idle and idle_sec < 15.0:
            # 2. User Return Detection (User resumed activity after >idle_threshold_seconds)
            self._is_user_idle = False
            logger.info("Workstation user presence transitioned to ACTIVE (Sir has returned).")
            self._handle_user_return(current_title)

        # 3. Active Window Focus Transition Tracking
        if current_title and current_title != self._last_active_window:
            self._last_active_window = current_title
            try:
                self.orchestrator.memory.remember_fact(
                    "active_desktop_context",
                    current_title,
                    category="workstation"
                )
            except Exception as e:
                logger.debug(f"Could not stage active desktop context: {e}")

            if hasattr(self.orchestrator, "broadcast_ambient_telemetry"):
                self.orchestrator.broadcast_ambient_telemetry({
                    "event": "window_focus",
                    "title": current_title,
                })

    def _handle_user_return(self, current_title: str):
        """Processes user return: stages context, updates HUD, and checks for imminent meetings."""
        now = time.time()
        # Cooldown of at least 5 minutes between return alerts to prevent flapping
        if now - self._last_return_alert < 300:
            return

        self._last_return_alert = now

        if current_title:
            try:
                self.orchestrator.memory.remember_fact(
                    "active_desktop_context",
                    current_title,
                    category="workstation"
                )
            except Exception:
                pass

        if hasattr(self.orchestrator, "broadcast_ambient_telemetry"):
            self.orchestrator.broadcast_ambient_telemetry({
                "event": "user_returned",
                "active_context": current_title,
            })

        # Check for urgent upcoming calendar events starting in <= 10 minutes
        try:
            events = self.orchestrator.memory.get_calendar_events()
            dt_now = datetime.datetime.now()
            for event in events:
                eid = event["id"]
                if eid in self._alerted_event_ids:
                    continue
                start_dt = self._parse_time(event["start_time"], event.get("created_at"))
                if not start_dt:
                    continue
                delta_minutes = int((start_dt - dt_now).total_seconds() // 60)
                if 0 <= delta_minutes <= 10:
                    title = event["title"]
                    loc = f" at {event['location']}" if event.get("location") else ""
                    time_desc = "shortly" if delta_minutes <= 1 else f"in {delta_minutes} minutes"
                    msg = f"Welcome back, Sir. Quick reminder: '{title}' commences {time_desc}{loc}."
                    if self.speak_proactive(msg):
                        self._mark_event_alerted(eid)
                        break
        except Exception as e:
            logger.debug(f"Return calendar check notice: {e}")

    def _is_on_battery(self) -> bool:
        """Returns True if workstation is running on battery power (unplugged)."""
        try:
            status = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                return status.ACLineStatus == 0
        except Exception:
            pass
        return False

    def _check_morning_briefing(self):
        """Delivers autonomous standup briefing if morning and not yet delivered today."""
        today_str = datetime.date.today().isoformat()
        if self._briefing_delivered_today:
            return

        now = datetime.datetime.now()
        # Eligible morning hours: 06:00 to 12:30 PM
        if 6 <= now.hour < 13:
            logger.info("Triggering autonomous morning briefing...")
            delivered = self.deliver_briefing()
            if delivered:
                self._briefing_delivered_today = True
                self.orchestrator.memory.remember_fact("last_briefing_date", today_str, category="system")

    def deliver_briefing(self) -> bool:
        """Compiles and speaks the executive standup briefing."""
        try:
            from jarvis.skills.calendar_manager import morning_protocol
            raw_telemetry = morning_protocol()

            prompt = (
                f"You are JARVIS delivering your executive spoken standup briefing to Sir. "
                f"Synthesize the following telemetry into a concise, 3-to-4 sentence natural spoken briefing. "
                f"Keep it elegant, composed, and direct for audio text-to-speech. "
                f"Do not read raw email IDs, bullet lists, or URLs.\n\n"
                f"Telemetry:\n{raw_telemetry}"
            )

            # Generate natural spoken summary through Brain
            spoken_summary = self.orchestrator.brain.process_query(prompt)
            if spoken_summary:
                return self.speak_proactive(spoken_summary)
            return False
        except Exception as e:
            logger.error(f"Failed to generate morning briefing: {e}")
            return False

    def _mark_event_alerted(self, eid: int):
        """Marks an event as alerted and persists to memory so restarts do not repeat it."""
        self._alerted_event_ids.add(eid)
        try:
            today_str = datetime.date.today().isoformat()
            self.orchestrator.memory.remember_fact(
                f"alerted_events_{today_str}",
                ",".join(str(i) for i in self._alerted_event_ids),
                category="system"
            )
        except Exception as e:
            logger.debug(f"Could not persist alerted event id {eid}: {e}")

    def _parse_time(self, timestr: str, created_at: Optional[str] = None) -> Optional[datetime.datetime]:
        """Parses various date/time formats into a datetime object."""
        timestr = timestr.strip()
        now = datetime.datetime.now()
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %I:%M %p",
            "%H:%M",
            "%I:%M %p",
            "%I %p",
        ]
        for fmt in formats:
            try:
                dt = datetime.datetime.strptime(timestr, fmt)
                if fmt in ("%H:%M", "%I:%M %p", "%I %p"):
                    if created_at:
                        try:
                            # created_at is typically "YYYY-MM-DD HH:MM:SS"
                            c_dt = datetime.datetime.fromisoformat(created_at.strip())
                            # If created on an earlier day without explicit date, it was for that creation day
                            dt = dt.replace(year=c_dt.year, month=c_dt.month, day=c_dt.day)
                        except Exception:
                            dt = dt.replace(year=now.year, month=now.month, day=now.day)
                    else:
                        dt = dt.replace(year=now.year, month=now.month, day=now.day)
                return dt
            except ValueError:
                pass
        return None

    def _check_calendar_events(self):
        """Checks for events starting within the next 15 minutes."""
        try:
            events = self.orchestrator.memory.get_calendar_events()
            now = datetime.datetime.now()

            for event in events:
                eid = event["id"]
                if eid in self._alerted_event_ids:
                    continue

                start_dt = self._parse_time(event["start_time"], event.get("created_at"))
                if not start_dt:
                    continue

                # Calculate delta in seconds and minutes
                delta_seconds = (start_dt - now).total_seconds()
                delta_minutes = int(delta_seconds // 60)

                # If event is already in the past (more than 10 minutes ago), mark alerted to prevent lingering
                if delta_seconds < -600:
                    self._mark_event_alerted(eid)
                    continue

                # Alert if event is 0 to 15 minutes away
                if 0 <= delta_minutes <= 15:
                    title = event["title"]
                    loc = f" at {event['location']}" if event.get("location") else ""
                    time_desc = "shortly" if delta_minutes <= 1 else f"in {delta_minutes} minutes"
                    msg = f"Pardon the interruption, Sir. You have '{title}' scheduled {time_desc}{loc}."
                    
                    if self.speak_proactive(msg):
                        self._mark_event_alerted(eid)
                        break  # Announce one at a time to prevent stacking
        except Exception as e:
            logger.debug(f"Calendar check notice: {e}")

    def _check_battery_status(self):
        """Alerts if battery drops below 20% while running on battery power."""
        now = time.time()
        if now - self._last_battery_alert < 1800:  # 30-minute cooldown
            return

        try:
            status = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                # ACLineStatus == 0 means running on battery (unplugged)
                if status.ACLineStatus == 0 and status.BatteryLifePercent <= 20:
                    pct = status.BatteryLifePercent
                    msg = f"Sir, workstation battery reserves have fallen to {pct}%. I recommend connecting to AC power."
                    if self.speak_proactive(msg):
                        self._last_battery_alert = now
        except Exception as e:
            logger.debug(f"Power check notice: {e}")

    def _check_ergonomics(self):
        """Provides an ambient posture and refreshment reminder after 2 hours of continuous work."""
        now = time.time()
        session_duration = now - self._session_start_time
        if session_duration >= 7200 and (now - self._last_break_reminder >= 7200):
            drink = self.orchestrator.memory.get_fact("late_night_drink") or "espresso"
            drink_clean = "an espresso" if "espresso" in drink.lower() else "a refreshment"
            msg = f"Sir, you have been engaged at the workstation for over two hours. May I suggest a brief stretch or taking a moment to enjoy {drink_clean}?"
            if self.speak_proactive(msg):
                self._last_break_reminder = now

    def _check_new_emails(self):
        """Checks for new unread incoming emails every 15 minutes."""
        now = time.time()
        if now - self._last_email_check < 900:  # 15 minutes
            return

        self._last_email_check = now
        try:
            from jarvis.skills.email_manager import get_unread_email_count
            current_count = get_unread_email_count()

            if self._last_unread_email_count is None:
                # First run initializes baseline count
                self._last_unread_email_count = current_count
                return

            if current_count > self._last_unread_email_count:
                delta = current_count - self._last_unread_email_count
                self._last_unread_email_count = current_count
                msg = f"Pardon me, Sir. You have received {delta} new unread email{'s' if delta > 1 else ''} in your inbox."
                self.speak_proactive(msg)
            else:
                self._last_unread_email_count = current_count
        except Exception as e:
            logger.debug(f"Email heartbeat check notice: {e}")
