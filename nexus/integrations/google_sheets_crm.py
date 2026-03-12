from __future__ import annotations

"""
Google Sheets CRM watcher.

Polls a spreadsheet tab for appended Facebook lead form rows and emits
normalized lead payloads to a callback.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_phone(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _is_truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    txt = str(value).strip().lower()
    if not txt:
        return default
    return txt not in {"0", "false", "no", "off", "disabled"}


def _norm_header(key: str) -> str:
    txt = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    return txt


def _split_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in str(full_name or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _to_int(value: Any, default: int = 0) -> int:
    try:
        digits = re.sub(r"[^\d]", "", str(value or ""))
        return int(digits) if digits else default
    except Exception:
        return default


class GoogleSheetCRMWatcher:
    def __init__(
        self,
        config: dict,
        on_row: Callable[[dict], Any],
        db_path: Path | None = None,
    ):
        self.config = config or {}
        self.on_row = on_row
        self.db_path = Path(db_path or DB_PATH)
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_error = ""
        self._last_sync_at = ""
        self._last_processed_row = 1
        self._processed_total = 0
        self._service = None
        self._drive = None
        self._spreadsheet_id_cache = ""
        self._init_db()

    @property
    def sheet_name(self) -> str:
        return (self.config.get("google_sheet_crm_name") or "Facebook Lead Ads Form CRM").strip()

    @property
    def poll_interval_seconds(self) -> int:
        return max(5, int(self.config.get("google_sheet_crm_poll_seconds", 12) or 12))

    @property
    def sheet_key(self) -> str:
        sid = self._spreadsheet_id_cache or self._resolve_spreadsheet_id_hint()
        base = sid or "unknown"
        return f"{base}:{self.sheet_name}"

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS google_sheet_sync_state (
                sheet_key TEXT PRIMARY KEY,
                last_row INTEGER DEFAULT 1,
                processed_rows INTEGER DEFAULT 0,
                last_sync_at TEXT DEFAULT '',
                last_error TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS google_sheet_processed_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sheet_key TEXT NOT NULL,
                row_number INTEGER NOT NULL,
                row_hash TEXT NOT NULL,
                lead_phone TEXT DEFAULT '',
                lead_email TEXT DEFAULT '',
                payload_json TEXT DEFAULT '{}',
                processed_at TEXT DEFAULT (datetime('now')),
                UNIQUE(sheet_key, row_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_sheet_processed_lookup
                ON google_sheet_processed_rows(sheet_key, row_number);
            """
        )
        conn.commit()
        conn.close()

    def _resolve_spreadsheet_id_hint(self) -> str:
        url = (
            self.config.get("google_sheet_crm_url")
            or self.config.get("fb_sheet_crm_url")
            or ""
        ).strip()
        if url:
            m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
            if m:
                return m.group(1)
        return (
            self.config.get("google_sheet_crm_id")
            or self.config.get("fb_sheet_crm_id")
            or ""
        ).strip()

    def _load_credentials(self):
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]

        raw = (self.config.get("google_service_account_json") or "").strip()
        if raw:
            try:
                info = json.loads(raw)
                return Credentials.from_service_account_info(info, scopes=scopes)
            except Exception:
                p = Path(raw).expanduser()
                if p.exists():
                    return Credentials.from_service_account_file(str(p), scopes=scopes)

        path_value = (
            self.config.get("google_service_account_file")
            or self.config.get("google_credentials_file")
            or ""
        ).strip()
        if path_value:
            p = Path(path_value).expanduser()
            if p.exists():
                return Credentials.from_service_account_file(str(p), scopes=scopes)

        env_value = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
        if env_value:
            env_path = Path(env_value).expanduser()
        else:
            env_path = None
        if env_path and env_path.exists() and env_path.is_file():
            return Credentials.from_service_account_file(str(env_path), scopes=scopes)

        raise RuntimeError(
            "Google Sheets CRM missing credentials. Set google_service_account_json or google_service_account_file."
        )

    def _build_services(self):
        from googleapiclient.discovery import build

        creds = self._load_credentials()
        self._service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _resolve_spreadsheet_id(self) -> str:
        if self._spreadsheet_id_cache:
            return self._spreadsheet_id_cache

        hinted = self._resolve_spreadsheet_id_hint()
        if hinted:
            self._spreadsheet_id_cache = hinted
            return hinted

        if not self._drive:
            self._build_services()

        target_name = self.sheet_name
        safe_name = target_name.replace("'", "\\'")
        query = (
            "mimeType='application/vnd.google-apps.spreadsheet' "
            f"and name='{safe_name}' and trashed=false"
        )
        result = self._drive.files().list(
            q=query,
            fields="files(id,name,createdTime)",
            pageSize=5,
            orderBy="createdTime desc",
        ).execute()
        files = result.get("files", [])
        if not files:
            raise RuntimeError(
                f"Could not find spreadsheet named '{target_name}'. "
                "Set google_sheet_crm_id or share the sheet with your service account."
            )
        self._spreadsheet_id_cache = files[0]["id"]
        return self._spreadsheet_id_cache

    def _load_state(self, sheet_key: str) -> dict:
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM google_sheet_sync_state WHERE sheet_key = ?",
            (sheet_key,),
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO google_sheet_sync_state (sheet_key, last_row, processed_rows) VALUES (?, 2, 0)",
                (sheet_key,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM google_sheet_sync_state WHERE sheet_key = ?",
                (sheet_key,),
            ).fetchone()
        conn.close()
        return dict(row) if row else {"last_row": 2, "processed_rows": 0}

    def _update_state(self, sheet_key: str, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields.keys())
        vals = list(fields.values()) + [sheet_key]
        conn = self._conn()
        conn.execute(f"UPDATE google_sheet_sync_state SET {sets} WHERE sheet_key = ?", vals)
        conn.commit()
        conn.close()

    def _is_processed(self, sheet_key: str, row_hash: str) -> bool:
        conn = self._conn()
        row = conn.execute(
            "SELECT 1 FROM google_sheet_processed_rows WHERE sheet_key = ? AND row_hash = ? LIMIT 1",
            (sheet_key, row_hash),
        ).fetchone()
        conn.close()
        return bool(row)

    def _mark_processed(self, sheet_key: str, item: dict):
        conn = self._conn()
        conn.execute(
            """
            INSERT OR IGNORE INTO google_sheet_processed_rows
            (sheet_key, row_number, row_hash, lead_phone, lead_email, payload_json, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sheet_key,
                int(item.get("row_number", 0)),
                item.get("row_hash", ""),
                item.get("lead_data", {}).get("phone", ""),
                item.get("lead_data", {}).get("email", ""),
                json.dumps(item.get("record", {}), default=str),
                _now_iso(),
            ),
        )
        conn.commit()
        conn.close()

    def _pick(self, record: dict, *keys: str) -> str:
        for key in keys:
            val = (record.get(key) or "").strip()
            if val:
                return val
        return ""

    def _extract_lead_data(self, record: dict) -> dict:
        first = self._pick(record, "first_name", "first", "contact_first_name")
        last = self._pick(record, "last_name", "last", "contact_last_name")
        if not first and not last:
            full = self._pick(record, "full_name", "name", "lead_name", "customer_name", "contact_name")
            first, last = _split_name(full)

        email = self._pick(record, "email", "email_address", "contact_email", "e_mail").lower()
        phone = _clean_phone(
            self._pick(
                record,
                "phone",
                "phone_number",
                "mobile",
                "mobile_number",
                "contact_phone",
            )
        )

        event_type = self._pick(
            record,
            "event_type",
            "type_of_event",
            "what_type_of_event",
            "event",
        )
        event_date = self._pick(record, "event_date", "preferred_date", "date_of_event")
        event_city = self._pick(record, "city", "event_city", "event_location_city")
        venue_location = self._pick(record, "venue_name", "venue_location", "venue", "location")
        source_detail = self._pick(
            record,
            "source_detail",
            "lead_source",
            "campaign_name",
            "ad_name",
            "form_name",
        ) or "Facebook Lead Form via Google Sheet CRM"
        form_name = self._pick(record, "form_name", "facebook_form", "lead_form_name")

        guest_count = _to_int(
            self._pick(
                record,
                "guest_count",
                "estimated_guest_count",
                "number_of_guests",
                "attendees",
                "estimated_attendees",
            ),
            default=0,
        )

        data = {
            "first_name": first,
            "last_name": last,
            "email": email,
            "phone": phone,
            "source": "facebook_ad",
            "source_detail": source_detail,
            "form_name": form_name,
            "campaign_id": self._pick(record, "campaign_id", "campaignid"),
            "ad_id": self._pick(record, "ad_id", "adid"),
            "ad_set_id": self._pick(record, "ad_set_id", "adset_id", "adsetid"),
            "event_type": event_type,
            "event_date": event_date,
            "event_city": event_city,
            "venue_location": venue_location,
            "guest_count": guest_count,
            "fbc": self._pick(record, "fbc"),
            "fbp": self._pick(record, "fbp"),
            "notes": self._pick(record, "notes", "message", "additional_info"),
        }
        return data

    def _build_items_sync(self, force: bool = False) -> tuple[list[dict], str, int]:
        if not self._service:
            self._build_services()
        spreadsheet_id = self._resolve_spreadsheet_id()
        sheet_key = f"{spreadsheet_id}:{self.sheet_name}"
        state = self._load_state(sheet_key)
        start_row = 2 if force else max(2, int(state.get("last_row") or 2))

        rng = f"'{self.sheet_name}'!A1:AZ"
        values = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=rng, majorDimension="ROWS")
            .execute()
            .get("values", [])
        )
        if not values:
            return [], sheet_key, start_row

        headers = [_norm_header(h) for h in values[0]]
        rows = values[1:]
        if (
            not force
            and int(state.get("processed_rows") or 0) == 0
            and not _is_truthy(self.config.get("google_sheet_crm_backfill", False), False)
        ):
            # First run defaults to realtime mode: start from the tail.
            start_row = max(start_row, len(rows) + 2)

        items: list[dict] = []
        absolute_row = 2
        for row in rows:
            record = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
            lead_data = self._extract_lead_data(record)
            row_payload = {
                "sheet_name": self.sheet_name,
                "spreadsheet_id": spreadsheet_id,
                "row_number": absolute_row,
                "record": record,
                "lead_data": lead_data,
            }
            row_hash = hashlib.sha256(
                json.dumps(
                    {
                        "row_number": absolute_row,
                        "record": record,
                    },
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            row_payload["row_hash"] = row_hash
            if absolute_row >= start_row:
                items.append(row_payload)
            absolute_row += 1

        return items, sheet_key, start_row

    async def poll_once(self, force: bool = False) -> dict:
        try:
            items, sheet_key, start_row = await asyncio.to_thread(self._build_items_sync, force)
            state = self._load_state(sheet_key)
            last_row = max(int(state.get("last_row") or 2), int(start_row or 2))
            processed = 0
            skipped = 0

            for item in items:
                row_number = int(item.get("row_number") or 0)
                lead_data = item.get("lead_data", {})
                has_identity = bool(lead_data.get("phone") or lead_data.get("email"))
                if not has_identity:
                    skipped += 1
                    last_row = max(last_row, row_number + 1)
                    continue
                if self._is_processed(sheet_key, item.get("row_hash", "")):
                    skipped += 1
                    last_row = max(last_row, row_number + 1)
                    continue

                result = self.on_row(item)
                if asyncio.iscoroutine(result):
                    result = await result

                # Treat exceptions/failed callbacks as retryable: do not advance pointer past failure.
                if isinstance(result, dict) and result.get("ok") is False:
                    break

                self._mark_processed(sheet_key, item)
                processed += 1
                self._processed_total += 1
                last_row = max(last_row, row_number + 1)

            self._last_sync_at = _now_iso()
            self._last_processed_row = max(self._last_processed_row, last_row)
            self._last_error = ""
            self._update_state(
                sheet_key,
                last_row=last_row,
                processed_rows=int(state.get("processed_rows") or 0) + processed,
                last_sync_at=self._last_sync_at,
                last_error="",
            )
            return {
                "ok": True,
                "processed": processed,
                "skipped": skipped,
                "last_row": last_row,
                "sheet_key": sheet_key,
            }
        except Exception as e:
            self._last_error = str(e)
            try:
                self._update_state(self.sheet_key, last_error=self._last_error, last_sync_at=_now_iso())
            except Exception:
                pass
            return {"ok": False, "error": self._last_error}

    async def run(self):
        self._running = True
        while self._running:
            res = await self.poll_once(force=False)
            if not res.get("ok"):
                await asyncio.sleep(min(30, self.poll_interval_seconds))
            else:
                await asyncio.sleep(self.poll_interval_seconds)

    def start(self):
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run())
        return self._task

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    def get_status(self) -> dict:
        sheet_id = self._spreadsheet_id_cache or self._resolve_spreadsheet_id_hint()
        sheet_key = f"{sheet_id or 'unknown'}:{self.sheet_name}"
        state = self._load_state(sheet_key)
        return {
            "enabled": str(self.config.get("google_sheet_crm_enabled", "true")).lower() not in ("0", "false", "no", "off"),
            "running": bool(self._task and not self._task.done()),
            "sheet_name": self.sheet_name,
            "spreadsheet_id": sheet_id,
            "poll_interval_seconds": self.poll_interval_seconds,
            "last_sync_at": state.get("last_sync_at") or self._last_sync_at,
            "last_error": state.get("last_error") or self._last_error,
            "last_row": int(state.get("last_row") or self._last_processed_row or 2),
            "processed_rows": int(state.get("processed_rows") or self._processed_total or 0),
        }


_watcher: GoogleSheetCRMWatcher | None = None


def init_google_sheets_crm(config: dict, on_row: Callable[[dict], Any], db_path: Path | None = None) -> GoogleSheetCRMWatcher:
    global _watcher
    _watcher = GoogleSheetCRMWatcher(config=config, on_row=on_row, db_path=db_path)
    return _watcher


def get_google_sheets_crm() -> GoogleSheetCRMWatcher | None:
    return _watcher
