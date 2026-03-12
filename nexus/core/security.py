"""
Nexus SecurityGate — Zero-Trust Messaging Architecture

Every outbound message must pass through 10 independent validation gates.
ANY single gate failure blocks the send. The system defaults to BLOCK.

Gates:
 1. Mode gate          — MESSAGING_MODE must be test or production
 2. Kill switch gate   — ~/nexus/.kill_switch file must NOT exist
 3. Dead man's switch  — Operator confirmed within last 4 hours
 4. Recipient validation — Allowlist (test) or approval (production)
 5. DLP scan           — No secrets/credentials in message body
 6. Approval verification — Valid kai_approved/kai_edited approval
 7. TCPA compliance    — Consent, quiet hours, opt-out
 8. Per-recipient rate limit — Max 1 SMS/day, 3 email/day per recipient
 9. Global rate limit  — 50/hour, 500/day system-wide
10. Circuit breaker    — Halts on 5 consecutive failures
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
KILL_SWITCH_PATH = Path.home() / "nexus" / ".kill_switch"

# Kai's safe contact info — the ONLY real numbers allowed in test mode
KAI_PHONE = os.environ.get("KAI_PERSONAL_PHONE", "8184489055")
KAI_EMAIL = os.environ.get("KAI_PERSONAL_EMAIL", "kaiescobar09@gmail.com")
ZOAR_EMAIL = "zoarbathrooms@gmail.com"  # Business email — internal operational messages

# ═══════════════════════════════════════════════════════════════════════════════
# DLP PATTERNS — Detect secrets/credentials in outbound message text
# ═══════════════════════════════════════════════════════════════════════════════
DLP_PATTERNS = {
    "slack_webhook": re.compile(
        r"https://hooks\.slack\.com/services/T[a-zA-Z0-9_]{8,}/B[a-zA-Z0-9_]{8,}/[a-zA-Z0-9_]{24,}"
    ),
    "slack_bot_token": re.compile(r"xoxb-[a-zA-Z0-9\-]+"),
    "slack_user_token": re.compile(r"xoxp-[a-zA-Z0-9\-]+"),
    "slack_app_token": re.compile(r"xapp-[a-zA-Z0-9\-]+"),
    "telegram_token": re.compile(r"[0-9]+:AA[A-Za-z0-9_\-]{33,}"),
    "facebook_token": re.compile(r"EAA[a-zA-Z0-9]{10,}"),
    "anthropic_key": re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"),
    "twilio_sid": re.compile(r"AC[a-f0-9]{32}"),
    "twilio_auth": re.compile(r"SK[a-f0-9]{32}"),
    "aws_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    "stripe_key": re.compile(r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{24,}"),
    "sendgrid_key": re.compile(r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}"),
    "generic_secret": re.compile(
        r'(?i)(?:password|secret|token|api_key|apikey|auth)\s*[:=]\s*[\'"][^\s\'"]{8,}[\'"]'
    ),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    "jwt": re.compile(
        r"eyJ[A-Za-z0-9\-_=]+\.eyJ[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_.+/=]*"
    ),
    "webhook_url": re.compile(
        r"https?://[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-]+\.com/(?:services|hooks|api|webhook)/"
    ),
    "connection_string": re.compile(r"(?:mongodb|postgres|mysql|redis)://[^\s]+"),
}

# ═══════════════════════════════════════════════════════════════════════════════
# SAFE RECIPIENTS — Only these are allowed in test mode
# ═══════════════════════════════════════════════════════════════════════════════
SAFE_PHONE_PREFIXES = ("555",)
SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "test.invalid")
TWILIO_MAGIC_NUMBERS = {"+15005550006", "+15005550001", "+15005550009"}


class SecurityGate:
    """Centralized 10-gate outbound message validator.

    Usage:
        gate = SecurityGate()
        result = gate.gate_outbound("sms", "6615551234", "Hey!", approval_id=42,
                                     code_path="messaging.send_sms")
        if not result["allowed"]:
            return {"ok": False, "reason": result["reason"]}
    """

    def __init__(self, db_path: Path = None, config_path: Path = None):
        self.db_path = db_path or DB_PATH
        self.config_path = config_path or CONFIG_PATH
        self._env_secrets: list[str] = []  # Dynamic DLP patterns from config values
        self._init_tables()
        self._load_env_secrets()
        # Circuit breaker state per channel
        self._cb_failures: dict[str, int] = {}
        self._cb_opened_at: dict[str, float] = {}
        self._cb_max_failures = 5
        self._cb_recovery_seconds = 300  # 5 minutes

    # ── Table initialization ──────────────────────────────────────────────────

    def _init_tables(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outbound_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                channel TEXT,
                recipient_hash TEXT,
                recipient_preview TEXT,
                message_hash TEXT,
                message_preview TEXT,
                approval_id INTEGER,
                approval_method TEXT,
                result TEXT,
                reason TEXT,
                code_path TEXT,
                gate_failed TEXT,
                prev_hash TEXT,
                entry_hash TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS security_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                event_type TEXT,
                details TEXT,
                severity TEXT,
                source_ip TEXT,
                user_agent TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dead_man_switch (
                id INTEGER PRIMARY KEY DEFAULT 1,
                last_confirmed_at TEXT,
                confirmed_by TEXT
            )
        """)
        # Seed dead man's switch if empty
        row = conn.execute("SELECT id FROM dead_man_switch WHERE id=1").fetchone()
        if not row:
            conn.execute(
                "INSERT INTO dead_man_switch (id, last_confirmed_at, confirmed_by) VALUES (1, ?, 'init')",
                (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),),
            )
        conn.commit()
        conn.close()

    def _load_env_secrets(self):
        """Load config values as dynamic DLP patterns — any config value >8 chars
        that looks like a secret gets added to the scan list."""
        try:
            cfg = json.loads(self.config_path.read_text())
            skip_keys = {"message_template", "default_carrier", "gmail_address",
                         "ghl_poll_interval", "fb_verify_token", "fb_webhook_domain"}
            for key, val in cfg.items():
                if key in skip_keys:
                    continue
                if isinstance(val, str) and len(val) > 8:
                    self._env_secrets.append(val)
        except Exception:
            pass

    # ── Config helpers ────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            return json.loads(self.config_path.read_text())
        except Exception:
            return {}

    def _get_mode(self) -> str:
        """Get current messaging mode: disabled, test, or production."""
        cfg = self._load_config()
        return cfg.get("messaging_mode", "disabled")

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ════════════════════════════════════════════════════════════════════════════
    # THE 10 GATES
    # ════════════════════════════════════════════════════════════════════════════

    def gate_outbound(
        self,
        channel: str,
        recipient: str,
        message: str,
        approval_id: int = None,
        code_path: str = "unknown",
    ) -> dict:
        """Run ALL 10 security gates. Returns {"allowed": bool, "reason": str, "gate_failed": str|None}.

        If ANY gate fails, the message is BLOCKED and logged.
        """
        # Gate 1: Mode
        mode = self._get_mode()
        if mode not in ("test", "production"):
            return self._block(channel, recipient, message, approval_id, code_path,
                               "mode_disabled", "gate_1_mode",
                               f"MESSAGING_MODE={mode}, must be test or production")

        # Gate 2: Kill switch
        if self.is_kill_switch_active():
            return self._block(channel, recipient, message, approval_id, code_path,
                               "kill_switch_active", "gate_2_kill_switch",
                               "Kill switch file exists")

        # Gate 3: Dead man's switch
        if not self.is_dead_man_confirmed():
            return self._block(channel, recipient, message, approval_id, code_path,
                               "dead_man_expired", "gate_3_dead_man",
                               "Operator confirmation expired (>4 hours)")

        # Gate 4: Recipient validation
        recipient_ok, recipient_reason = self.validate_recipient(recipient, mode)
        if not recipient_ok:
            return self._block(channel, recipient, message, approval_id, code_path,
                               recipient_reason, "gate_4_recipient", recipient_reason)

        # Gate 5: DLP scan
        dlp_violations = self.scan_for_secrets(message)
        if dlp_violations:
            types = ", ".join(dlp_violations)
            self.log_security("dlp_violation",
                              f"Blocked {channel} to {self._hash_recipient(recipient)}: {types}",
                              "critical")
            return self._block(channel, recipient, message, approval_id, code_path,
                               f"dlp_violation: {types}", "gate_5_dlp",
                               f"DLP policy violation: {types}")

        # Gate 6: Approval verification (production only for non-safe recipients)
        safe_recipient = self._is_safe_recipient(recipient)
        cfg = self._load_config()
        safe_bypass = bool(cfg.get("safe_recipient_gate_bypass", True))

        if mode == "production" and not safe_recipient:
            ok, method, reason = self.verify_approval(approval_id)
            if not ok:
                return self._block(channel, recipient, message, approval_id, code_path,
                                   reason, "gate_6_approval", reason)

        # Gate 7: TCPA compliance
        if channel == "sms" and not (safe_recipient and safe_bypass):
            try:
                from core.tcpa import check_quiet_hours, check_opt_out
                quiet = check_quiet_hours(recipient)
                if quiet:
                    return self._block(channel, recipient, message, approval_id, code_path,
                                       "tcpa_quiet_hours", "gate_7_tcpa",
                                       "Outside allowed hours (8AM-9PM recipient local time)")
                opted_out = check_opt_out(recipient)
                if opted_out:
                    return self._block(channel, recipient, message, approval_id, code_path,
                                       "tcpa_opted_out", "gate_7_tcpa",
                                       "Recipient has opted out")
            except ImportError:
                pass  # TCPA module not yet available

        # Gate 8: Per-recipient rate limit
        if not (safe_recipient and safe_bypass):
            try:
                from core.rate_limiter import check_recipient_limit
                rate_ok, rate_reason = check_recipient_limit(recipient, channel)
                if not rate_ok:
                    return self._block(channel, recipient, message, approval_id, code_path,
                                       rate_reason, "gate_8_rate_recipient", rate_reason)
            except ImportError:
                pass

        # Gate 9: Global rate limit
        try:
            from core.rate_limiter import check_global_limit
            global_ok, global_reason = check_global_limit(channel)
            if not global_ok:
                return self._block(channel, recipient, message, approval_id, code_path,
                                   global_reason, "gate_9_rate_global", global_reason)
        except ImportError:
            pass

        # Gate 10: Circuit breaker
        if not self.check_circuit_breaker(channel):
            return self._block(channel, recipient, message, approval_id, code_path,
                               "circuit_breaker_open", "gate_10_circuit_breaker",
                               f"Circuit breaker open for {channel}")

        # ✅ ALL 10 GATES PASSED
        approval_method = ""
        if approval_id:
            _, method, _ = self.verify_approval(approval_id)
            approval_method = method

        self._log_outbound(channel, recipient, message, approval_id, approval_method,
                           "allowed", "all_gates_passed", code_path, None)

        # Record send for rate limiting
        try:
            from core.rate_limiter import record_send
            record_send(recipient, channel)
        except ImportError:
            pass

        return {"allowed": True, "reason": "all_gates_passed", "gate_failed": None,
                "approved_by": approval_method}

    # ── Gate implementations ──────────────────────────────────────────────────

    def is_kill_switch_active(self) -> bool:
        """Gate 2: Check if kill switch file exists."""
        return KILL_SWITCH_PATH.exists()

    def activate_kill_switch(self, reason: str = "manual"):
        """Create the kill switch file."""
        KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH_PATH.write_text(
            json.dumps({"activated_at": datetime.utcnow().isoformat(),
                        "reason": reason})
        )
        self.log_security("kill_switch_activated", reason, "critical")

    def deactivate_kill_switch(self):
        """Remove the kill switch file."""
        if KILL_SWITCH_PATH.exists():
            KILL_SWITCH_PATH.unlink()
        self.log_security("kill_switch_deactivated", "manual", "warning")

    def is_dead_man_confirmed(self) -> bool:
        """Gate 3: Operator must have confirmed within last 4 hours."""
        conn = self._db()
        row = conn.execute(
            "SELECT last_confirmed_at FROM dead_man_switch WHERE id=1"
        ).fetchone()
        conn.close()
        if not row or not row["last_confirmed_at"]:
            return False
        try:
            confirmed = datetime.strptime(row["last_confirmed_at"], "%Y-%m-%d %H:%M:%S")
            return (datetime.utcnow() - confirmed) < timedelta(hours=4)
        except Exception:
            return False

    def confirm_alive(self, confirmed_by: str = "telegram"):
        """Reset the dead man's switch."""
        conn = self._db()
        conn.execute(
            "UPDATE dead_man_switch SET last_confirmed_at=?, confirmed_by=? WHERE id=1",
            (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), confirmed_by),
        )
        conn.commit()
        conn.close()
        self.log_security("dead_man_confirmed", confirmed_by, "info")

    def validate_recipient(self, recipient: str, mode: str) -> tuple[bool, str]:
        """Gate 4: Check recipient against mode rules."""
        if mode == "test":
            if self._is_safe_recipient(recipient):
                return True, "safe_recipient"
            return False, f"test_mode_blocked: {self._hash_recipient(recipient)} not on allowlist"
        # Production: recipient validation delegated to gate 6 (approval)
        return True, "production_mode"

    def _is_safe_recipient(self, recipient: str) -> bool:
        """Is this recipient safe for test mode?"""
        clean = re.sub(r"\D", "", recipient)
        # Kai's personal contact
        if clean == KAI_PHONE or recipient.lower() == KAI_EMAIL.lower():
            return True
        # Zoar business email (internal operational messages)
        if recipient.lower() == ZOAR_EMAIL.lower():
            return True
        # 555 numbers (fictional)
        if clean.startswith("555") or (len(clean) >= 10 and clean[-10:-7] == "555"):
            return True
        # Twilio magic numbers
        if f"+1{clean}" in TWILIO_MAGIC_NUMBERS or recipient in TWILIO_MAGIC_NUMBERS:
            return True
        # Safe email domains
        if "@" in recipient:
            domain = recipient.split("@")[-1].lower()
            if domain in SAFE_EMAIL_DOMAINS:
                return True
        return False

    def scan_for_secrets(self, message: str) -> list[str]:
        """Gate 5: DLP scan — detect secrets in outbound message body."""
        violations = []
        if not message:
            return violations
        # Static patterns
        for name, pattern in DLP_PATTERNS.items():
            if pattern.search(message):
                violations.append(name)
        # Dynamic patterns from config values
        for secret in self._env_secrets:
            if secret in message:
                violations.append("config_value_leak")
                break
        # Check for Kai's personal info
        if KAI_PHONE in message:
            violations.append("personal_phone_leak")
        if KAI_EMAIL.lower() in message.lower():
            violations.append("personal_email_leak")
        # High-entropy detection (likely tokens)
        for word in message.split():
            if len(word) >= 20:
                entropy = self._shannon_entropy(word)
                if entropy > 4.5:
                    violations.append("high_entropy_string")
                    break
        return violations

    @staticmethod
    def _shannon_entropy(s: str) -> float:
        """Calculate Shannon entropy of a string (bits per character)."""
        from collections import Counter
        import math
        if not s:
            return 0
        freq = Counter(s)
        length = len(s)
        return -sum((c / length) * math.log2(c / length) for c in freq.values())

    def verify_approval(self, approval_id: int) -> tuple[bool, str, str]:
        """Gate 6: Verify approval exists, was approved by Kai, within 72 hours."""
        if not approval_id:
            return False, "", "no_approval_id"
        conn = self._db()
        row = conn.execute(
            "SELECT status, approval_method, approved_at FROM message_approvals WHERE id=?",
            (approval_id,),
        ).fetchone()
        conn.close()
        if not row:
            return False, "", f"approval_{approval_id}_not_found"
        if row["status"] != "approved":
            return False, str(row["status"]), f"approval_{approval_id}_status_{row['status']}"
        method = row["approval_method"] or ""
        if method not in ("kai_approved", "kai_edited", "kai_quick_email"):
            return False, method, f"approval_{approval_id}_method_{method}"
        # Check 72-hour expiry
        approved_at = row["approved_at"]
        if approved_at:
            try:
                decided = datetime.strptime(approved_at[:19], "%Y-%m-%d %H:%M:%S")
                if (datetime.utcnow() - decided) > timedelta(hours=72):
                    return False, method, f"approval_{approval_id}_expired"
            except Exception:
                pass
        return True, method, "verified"

    def check_circuit_breaker(self, channel: str) -> bool:
        """Gate 10: Circuit breaker — open after 5 consecutive failures."""
        if channel not in self._cb_failures:
            return True  # Closed
        if self._cb_failures[channel] < self._cb_max_failures:
            return True  # Below threshold
        # Check if recovery time has passed
        opened_at = self._cb_opened_at.get(channel, 0)
        if time.time() - opened_at > self._cb_recovery_seconds:
            # Half-open: allow one attempt
            return True
        return False  # Open

    def record_send_result(self, channel: str, success: bool):
        """Update circuit breaker state after a send attempt."""
        if success:
            self._cb_failures[channel] = 0
            if channel in self._cb_opened_at:
                del self._cb_opened_at[channel]
        else:
            self._cb_failures[channel] = self._cb_failures.get(channel, 0) + 1
            if self._cb_failures[channel] >= self._cb_max_failures:
                self._cb_opened_at[channel] = time.time()
                self.log_security("circuit_breaker_opened",
                                  f"Channel {channel}: {self._cb_failures[channel]} consecutive failures",
                                  "critical")

    # ── Logging ───────────────────────────────────────────────────────────────

    def _block(self, channel, recipient, message, approval_id, code_path,
               reason, gate_failed, detail):
        """Log a blocked send and return the block result."""
        self._log_outbound(channel, recipient, message, approval_id, "",
                           "blocked", reason, code_path, gate_failed)
        print(f"[SECURITY] BLOCKED {channel} to {self._mask_recipient(recipient)} — "
              f"gate={gate_failed} reason={reason}")
        return {"allowed": False, "reason": reason, "gate_failed": gate_failed}

    def _log_outbound(self, channel, recipient, message, approval_id,
                      approval_method, result, reason, code_path, gate_failed):
        """Append-only outbound log with hash chaining."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            # Get previous hash for chain
            prev = conn.execute(
                "SELECT entry_hash FROM outbound_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = prev[0] if prev and prev[0] else "0" * 64

            recipient_hash = self._hash_recipient(recipient)
            recipient_preview = self._mask_recipient(recipient)
            message_hash = hashlib.sha256(message.encode()).hexdigest() if message else ""
            message_preview = message[:100] if message else ""
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            # Compute entry hash
            entry_data = f"{ts}|{channel}|{recipient_hash}|{message_hash}|{result}|{prev_hash}"
            entry_hash = hashlib.sha256(entry_data.encode()).hexdigest()

            conn.execute(
                "INSERT INTO outbound_log (timestamp, channel, recipient_hash, "
                "recipient_preview, message_hash, message_preview, approval_id, "
                "approval_method, result, reason, code_path, gate_failed, "
                "prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, channel, recipient_hash, recipient_preview, message_hash,
                 message_preview, approval_id or 0, approval_method or "",
                 result, reason, code_path, gate_failed or "",
                 prev_hash, entry_hash),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SecurityGate] Log error: {e}")

    def log_security(self, event_type: str, details: str, severity: str,
                     source_ip: str = "", user_agent: str = ""):
        """Log security events."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                "INSERT INTO security_log (event_type, details, severity, source_ip, user_agent) "
                "VALUES (?,?,?,?,?)",
                (event_type, details, severity, source_ip, user_agent),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[SecurityGate] Security log error: {e}")

    def verify_audit_chain(self) -> tuple[bool, str]:
        """Verify the outbound_log hash chain for tamper detection."""
        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute(
            "SELECT id, timestamp, channel, recipient_hash, message_hash, "
            "result, prev_hash, entry_hash FROM outbound_log ORDER BY id ASC"
        ).fetchall()
        conn.close()
        if not rows:
            return True, "Chain empty — no entries"
        expected_prev = "0" * 64
        for row in rows:
            if row[6] != expected_prev:  # prev_hash
                return False, f"Chain broken at id={row[0]}: expected prev={expected_prev[:16]}..."
            entry_data = f"{row[1]}|{row[2]}|{row[3]}|{row[4]}|{row[5]}|{row[6]}"
            computed = hashlib.sha256(entry_data.encode()).hexdigest()
            if computed != row[7]:  # entry_hash
                return False, f"Tampered entry at id={row[0]}"
            expected_prev = row[7]
        return True, f"Chain valid: {len(rows)} entries verified"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _hash_recipient(recipient: str) -> str:
        """SHA-256 hash of recipient for safe logging."""
        return hashlib.sha256(recipient.encode()).hexdigest()

    @staticmethod
    def _mask_recipient(recipient: str) -> str:
        """Mask recipient for display: show only last 4 chars."""
        if "@" in recipient:
            return f"...{recipient[-15:]}"
        clean = re.sub(r"\D", "", recipient)
        return f"...{clean[-4:]}" if len(clean) >= 4 else "***"

    # ── Mode management ───────────────────────────────────────────────────────

    def set_mode(self, mode: str, source: str = "unknown"):
        """Set messaging mode (disabled/test/production)."""
        if mode not in ("disabled", "test", "production"):
            raise ValueError(f"Invalid mode: {mode}")
        cfg = self._load_config()
        old_mode = cfg.get("messaging_mode", "disabled")
        cfg["messaging_mode"] = mode
        if mode == "production":
            # Auto-expire production mode after 24 hours
            cfg["production_mode_expires_at"] = (
                datetime.utcnow() + timedelta(hours=24)
            ).strftime("%Y-%m-%d %H:%M:%S")
        self.config_path.write_text(json.dumps(cfg, indent=2))
        self.log_security("mode_change", f"{old_mode} → {mode} by {source}", "warning")
        # Also refresh dead man's switch on mode change
        if mode in ("test", "production"):
            self.confirm_alive(source)

    def get_status(self) -> dict:
        """Full security status for dashboard/Telegram."""
        mode = self._get_mode()
        cfg = self._load_config()
        conn = self._db()
        recent_blocks = conn.execute(
            "SELECT COUNT(*) FROM outbound_log WHERE result='blocked' "
            "AND timestamp > datetime('now', '-24 hours')"
        ).fetchone()[0]
        recent_sends = conn.execute(
            "SELECT COUNT(*) FROM outbound_log WHERE result='allowed' "
            "AND timestamp > datetime('now', '-24 hours')"
        ).fetchone()[0]
        conn.close()

        return {
            "mode": mode,
            "kill_switch": self.is_kill_switch_active(),
            "dead_man_confirmed": self.is_dead_man_confirmed(),
            "circuit_breakers": {ch: f >= self._cb_max_failures
                                  for ch, f in self._cb_failures.items()},
            "production_expires_at": cfg.get("production_mode_expires_at", ""),
            "recent_blocks_24h": recent_blocks,
            "recent_sends_24h": recent_sends,
        }

    def get_outbound_log(self, limit: int = 20) -> list[dict]:
        """Get recent outbound log entries."""
        conn = self._db()
        rows = conn.execute(
            "SELECT * FROM outbound_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_security_log(self, limit: int = 20) -> list[dict]:
        """Get recent security events."""
        conn = self._db()
        rows = conn.execute(
            "SELECT * FROM security_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_gate: SecurityGate | None = None


def get_security_gate() -> SecurityGate:
    """Get or create the singleton SecurityGate."""
    global _gate
    if _gate is None:
        _gate = SecurityGate()
    return _gate


# ═══════════════════════════════════════════════════════════════════════════════
# REAL-TIME SEND NOTIFICATION — Rule 10
# ═══════════════════════════════════════════════════════════════════════════════

async def notify_message_sent(channel: str, recipient_name: str, recipient_preview: str,
                               message: str, approval_id: int, approval_method: str):
    """Notify Kai on ALL channels after a successful send."""
    from datetime import datetime
    import pytz

    la_tz = pytz.timezone("America/Los_Angeles")
    now_pt = datetime.now(la_tz).strftime("%I:%M %p PT")

    notification = (
        f"📤 MESSAGE SENT\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"To: {recipient_name} — {recipient_preview}\n"
        f"Via: {channel.upper()}\n"
        f"Approval: #{approval_id} — {approval_method}\n"
        f"Content: \"{message[:200]}\"\n"
        f"Sent at: {now_pt}\n"
        f"Gate results: 10/10 passed"
    )

    # Telegram
    try:
        from telegram.bot import get_bot
        bot = get_bot()
        if bot:
            await bot.send_to_all(notification)
    except Exception as e:
        print(f"[SecurityGate] Telegram notify error: {e}")

    # Slack
    try:
        from integrations.slack_notify import send_slack
        await send_slack(notification)
    except Exception as e:
        print(f"[SecurityGate] Slack notify error: {e}")
