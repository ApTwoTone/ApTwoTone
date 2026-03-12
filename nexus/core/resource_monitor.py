"""
Nexus Mac Mini Resource Monitor — Protects Ollama from thermal throttling.

Monitors CPU and RAM pressure in real time. Automatically scales Ollama
workers down when resources are stressed. Sends Telegram alert at critical.

Usage:
    from core.resource_monitor import ResourceMonitor
    monitor = ResourceMonitor()
    monitor.check()  # Returns recommended Ollama worker scale factor
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

log = logging.getLogger("resource_monitor")


class ResourceMonitor:
    """Monitors Mac Mini CPU/RAM and recommends Ollama worker scaling."""

    # Thresholds
    CPU_HIGH = 80       # Reduce workers by 25%
    CPU_CRITICAL = 95   # Pause all Ollama workers
    RAM_WARNING = 75    # Reduce workers by 50%
    RAM_CRITICAL = 90   # Pause all Ollama workers + Telegram alert

    def __init__(self):
        self._last_check = 0.0
        self._check_interval = 5.0  # seconds
        self._last_alert_time = 0.0
        self._alert_cooldown = 300.0  # 5 min between alerts
        self._current_scale = 1.0
        self._paused = False

    def check(self) -> float:
        """Check resource pressure and return scale factor (0.0 to 1.0).

        Returns:
            1.0 = full capacity
            0.75 = reduce 25% (CPU high)
            0.50 = reduce 50% (RAM warning)
            0.0 = pause all (critical pressure)
        """
        now = time.time()
        if now - self._last_check < self._check_interval:
            return self._current_scale

        self._last_check = now
        cpu = self._get_cpu_usage()
        ram = self._get_ram_pressure()

        if cpu >= self.CPU_CRITICAL or ram >= self.RAM_CRITICAL:
            scale = 0.0
            self._paused = True
            if now - self._last_alert_time > self._alert_cooldown:
                self._send_alert(cpu, ram)
                self._last_alert_time = now
            log.warning("CRITICAL: CPU=%.0f%% RAM=%.0f%% — Ollama workers PAUSED", cpu, ram)
        elif ram >= self.RAM_WARNING:
            scale = 0.5
            self._paused = False
            log.info("RAM warning: %.0f%% — Ollama workers at 50%%", ram)
        elif cpu >= self.CPU_HIGH:
            scale = 0.75
            self._paused = False
            log.info("CPU high: %.0f%% — Ollama workers at 75%%", cpu)
        else:
            scale = 1.0
            if self._paused:
                log.info("Resources recovered: CPU=%.0f%% RAM=%.0f%% — resuming Ollama", cpu, ram)
            self._paused = False

        self._current_scale = scale
        return scale

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_status(self) -> dict:
        """Full status report."""
        return {
            "cpu_percent": self._get_cpu_usage(),
            "ram_percent": self._get_ram_pressure(),
            "scale_factor": self._current_scale,
            "paused": self._paused,
            "thresholds": {
                "cpu_high": self.CPU_HIGH,
                "cpu_critical": self.CPU_CRITICAL,
                "ram_warning": self.RAM_WARNING,
                "ram_critical": self.RAM_CRITICAL,
            },
        }

    @staticmethod
    def _get_cpu_usage() -> float:
        """Get CPU usage percentage via top (macOS)."""
        try:
            result = subprocess.run(
                ["top", "-l", "1", "-n", "0", "-stats", "cpu"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split("\n"):
                if "CPU usage" in line:
                    # "CPU usage: 25.0% user, 10.0% sys, 65.0% idle"
                    parts = line.split(",")
                    for part in parts:
                        if "idle" in part:
                            idle = float(part.strip().split("%")[0].strip().split()[-1])
                            return 100.0 - idle
        except Exception as e:
            log.debug("CPU check failed: %s", e)
        return 0.0

    @staticmethod
    def _get_ram_pressure() -> float:
        """Get RAM usage percentage via vm_stat (macOS)."""
        try:
            result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            )
            pages = {}
            for line in result.stdout.split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    val = val.strip().rstrip(".")
                    try:
                        pages[key.strip()] = int(val)
                    except ValueError:
                        pass

            # Page size is 16384 on Apple Silicon, 4096 on Intel
            page_size = 16384
            free = pages.get("Pages free", 0) * page_size
            active = pages.get("Pages active", 0) * page_size
            inactive = pages.get("Pages inactive", 0) * page_size
            wired = pages.get("Pages wired down", 0) * page_size
            compressed = pages.get("Pages occupied by compressor", 0) * page_size

            total = free + active + inactive + wired + compressed
            if total == 0:
                return 0.0
            used = active + wired + compressed
            return (used / total) * 100.0
        except Exception as e:
            log.debug("RAM check failed: %s", e)
        return 0.0

    def _send_alert(self, cpu: float, ram: float):
        """Send Telegram alert for critical resource pressure."""
        try:
            from pathlib import Path
            import json
            cfg_path = Path.home() / ".nexus" / "config.json"
            cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            token = cfg.get("telegram_token", "")
            chat_ids = cfg.get("telegram_chat_ids", [])
            if not token or not chat_ids:
                return

            import urllib.request
            msg = (
                "RESOURCE ALERT - Mac Mini\n\n"
                "CPU: %.0f%%\nRAM: %.0f%%\n\n"
                "All Ollama workers PAUSED.\n"
                "Workers will auto-resume when resources recover."
            ) % (cpu, ram)

            for chat_id in chat_ids:
                url = "https://api.telegram.org/bot%s/sendMessage" % token
                data = json.dumps({"chat_id": chat_id, "text": msg}).encode()
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            log.error("Failed to send Telegram alert: %s", e)


# Global singleton
_monitor: Optional[ResourceMonitor] = None


def get_monitor() -> ResourceMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ResourceMonitor()
    return _monitor
