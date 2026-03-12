from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore


CONFIG_PATH = Path.home() / ".nexus" / "config.json"


@dataclass(frozen=True)
class SendWindow:
    start_hour: int = 8
    end_hour: int = 17
    timezone: str = "America/Los_Angeles"

    def as_dict(self) -> Dict[str, object]:
        return {
            "start": f"{self.start_hour:02d}:00",
            "end": f"{self.end_hour:02d}:00",
            "timezone": self.timezone,
        }


def _safe_int(v: object, default: int) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except Exception:
        return default


def _load_config() -> Dict[str, object]:
    try:
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def load_send_window(default: SendWindow = SendWindow()) -> SendWindow:
    cfg = _load_config()
    raw = cfg.get("send_window")
    if not isinstance(raw, dict):
        return default
    start_raw = str(raw.get("start", f"{default.start_hour:02d}:00"))
    end_raw = str(raw.get("end", f"{default.end_hour:02d}:00"))
    tz = str(raw.get("timezone", default.timezone)).strip() or default.timezone
    try:
        sh = _safe_int(start_raw.split(":")[0], default.start_hour)
        eh = _safe_int(end_raw.split(":")[0], default.end_hour)
    except Exception:
        sh, eh = default.start_hour, default.end_hour
    sh = max(0, min(sh, 23))
    eh = max(1, min(eh, 24))
    if eh <= sh:
        eh = min(24, sh + 1)
    return SendWindow(start_hour=sh, end_hour=eh, timezone=tz)


def now_in_window() -> Tuple[bool, datetime, SendWindow]:
    win = load_send_window()
    now_local = datetime.now(ZoneInfo(win.timezone))
    ok = win.start_hour <= now_local.hour < win.end_hour
    return ok, now_local, win
