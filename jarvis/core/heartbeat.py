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


class JarvisHeartbeat:
    """Ambient background monitor coordinating proactive spoken alerts and briefings."""

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

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Alert deduplication sets
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

            time.sleep(self.tick_interval)

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

    def _parse_time(self, timestr: str) -> Optional[datetime.datetime]:
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

                start_dt = self._parse_time(event["start_time"])
                if not start_dt:
                    continue

                # Calculate delta in minutes
                delta_seconds = (start_dt - now).total_seconds()
                delta_minutes = int(delta_seconds // 60)

                # Alert if event is 1 to 15 minutes away
                if 0 <= delta_minutes <= 15:
                    title = event["title"]
                    loc = f" at {event['location']}" if event.get("location") else ""
                    time_desc = "shortly" if delta_minutes <= 1 else f"in {delta_minutes} minutes"
                    msg = f"Pardon the interruption, Sir. You have '{title}' scheduled {time_desc}{loc}."
                    
                    if self.speak_proactive(msg):
                        self._alerted_event_ids.add(eid)
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
