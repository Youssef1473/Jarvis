"""Windows Win32 Thread Affinity and Real-Time Priority Scheduler for Jarvis 2.0.

Pins performance-critical voice threads (Kokoro-82M neural synthesis, sounddevice WASAPI
playback, faster-whisper STT, and wake-word VAD loops) to Intel Performance (P) cores,
preventing Windows 11 Thread Director from parking real-time audio threads onto Efficient (E) cores.
"""
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger("Jarvis.Audio.Affinity")

# Win32 Priority Constants
THREAD_PRIORITY_NORMAL = 0
THREAD_PRIORITY_ABOVE_NORMAL = 1
THREAD_PRIORITY_HIGHEST = 2
THREAD_PRIORITY_TIME_CRITICAL = 15


def get_pcore_affinity_mask() -> int:
    """Calculates the processor affinity bitmask for Intel/AMD Performance (P) cores.

    On Intel 12th/13th/14th Gen hybrid laptop processors (e.g., i5-12500H with 12 physical cores,
    16 logical threads):
    - Cores 0-7: 4 P-Cores with Hyper-Threading (Mask: 0x00FF)
    - Cores 8-15: 8 E-Cores, single-threaded (Mask: 0xFF00)
    """
    logical_cpus = os.cpu_count() or 1
    if logical_cpus >= 16:
        # 16-thread hybrid CPU: Pin to logical threads 0-7 (P-cores)
        return 0x00FF
    elif logical_cpus >= 12:
        # 12-thread CPU: Pin to logical threads 0-7
        return 0x00FF
    elif logical_cpus > 4:
        # Standard multi-core CPU: use first min(logical_cpus, 8) cores
        return (1 << min(logical_cpus, 8)) - 1
    else:
        # Compact CPU (<=4 cores): allow all available cores
        return (1 << logical_cpus) - 1


def pin_thread_to_pcores(thread_handle: Optional[int] = None, mask: Optional[int] = None) -> bool:
    """Pins a thread to high-performance P-cores using SetThreadAffinityMask."""
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        target_handle = ctypes.c_void_p(-2) if thread_handle is None else ctypes.c_void_p(thread_handle)
        target_mask = mask if mask is not None else get_pcore_affinity_mask()

        k32.SetThreadAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k32.SetThreadAffinityMask.restype = ctypes.c_size_t

        prev_mask = k32.SetThreadAffinityMask(target_handle, ctypes.c_size_t(target_mask))
        if prev_mask != 0:
            logger.debug(
                "Thread affinity pinned to P-Cores: mask 0x%X (previous: 0x%X)",
                target_mask, prev_mask
            )
            return True
        return False
    except Exception as e:
        logger.debug("Failed to set thread affinity mask: %s", e)
        return False


def elevate_thread_priority(thread_handle: Optional[int] = None, priority: int = THREAD_PRIORITY_HIGHEST) -> bool:
    """Elevates thread priority on Windows (default THREAD_PRIORITY_HIGHEST = 2)."""
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        target_handle = ctypes.c_void_p(-2) if thread_handle is None else ctypes.c_void_p(thread_handle)

        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k32.SetThreadPriority.restype = ctypes.c_int

        res = k32.SetThreadPriority(target_handle, priority)
        if res != 0:
            logger.debug("Thread priority elevated to level %d", priority)
            return True
        return False
    except Exception as e:
        logger.debug("Failed to set thread priority: %s", e)
        return False


def pin_and_elevate_current_thread(tag: str = "AudioThread", time_critical: bool = False) -> bool:
    """Convenience helper to pin the current calling thread to P-Cores and elevate its priority."""
    if sys.platform != "win32":
        return False

    priority = THREAD_PRIORITY_TIME_CRITICAL if time_critical else THREAD_PRIORITY_HIGHEST
    p_pinned = pin_thread_to_pcores()
    p_elevated = elevate_thread_priority(priority=priority)

    if p_pinned or p_elevated:
        logger.info(
            "[%s] High-Performance Audio Affinity active: P-Cores pinned (0x%X), priority=%d",
            tag, get_pcore_affinity_mask(), priority
        )
        return True
    return False
