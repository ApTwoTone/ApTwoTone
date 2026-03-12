"""
Lead identity reconciliation.

Hard-merges duplicate lead identities across phone/email aliases by:
- Picking one canonical lead id
- Repointing lead-linked records to canonical
- Archiving duplicate lead rows
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

DB_PATH = Path.home() / ".nexus" / "memory.db"

SMS_GATEWAY_DOMAINS = {
    "tmomail.net",
    "txt.att.net",
    "vtext.com",
    "messaging.sprintpcs.com",
    "mms.cricketwireless.net",
    "mymetropcs.com",
    "sms.myboostmobile.com",
    "msg.fi.google.com",
    "vmobl.com",
}


def _conn(db_path: Path | None = None):
    conn = sqlite3.connect(str(db_path or DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _normalize_email(email_addr: str) -> str:
    return (email_addr or "").strip().lower()


def _email_local(email_addr: str) -> str:
    s = _normalize_email(email_addr)
    if "@" not in s:
        return ""
    return s.split("@", 1)[0]


def _is_sms_gateway_email(email_addr: str) -> bool:
    s = _normalize_email(email_addr)
    if "@" not in s:
        return False
    return s.split("@", 1)[1] in SMS_GATEWAY_DOMAINS


def _phone_from_email_alias(email_addr: str) -> str:
    s = _normalize_email(email_addr)
    if "@" not in s:
        return ""
    local, domain = s.split("@", 1)
    if domain not in SMS_GATEWAY_DOMAINS:
        return ""
    digits = "".join(ch for ch in local if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _source_rank(source: str) -> int:
    s = (source or "").strip().lower()
    if not s:
        return 0
    if s.startswith("facebook"):
        return 7
    if s.startswith("website"):
        return 6
    if s.startswith("referral"):
        return 6
    if s.startswith("ghl") or s.startswith("api"):
        return 5
    if s.startswith("manual"):
        return 4
    if s.startswith("google_voice") or s.startswith("sms"):
        return 3
    if s.startswith("email"):
        return 2
    return 1


def _is_placeholder_name(name: str, email_addr: str = "", phone: str = "") -> bool:
    n = " ".join((name or "").strip().split()).lower()
    if not n:
        return True
    if n in {"unknown", "lead", "contact", "n/a", "na", "none", "null", "test"}:
        return True
    if re.fullmatch(r"[\d\W_]+", n):
        return True
    local = _email_local(email_addr)
    if local and n == local:
        return True
    digits = _normalize_phone(phone)
    if digits and n == digits:
        return True
    return False


def _split_name(full_name: str) -> tuple[str, str]:
    text = " ".join((full_name or "").strip().split())
    if not text:
        return "", ""
    parts = text.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _best_name(row: Dict[str, Any]) -> str:
    email_addr = row.get("email", "")
    phone = row.get("phone", "")
    full = " ".join((row.get("full_name", "") or "").strip().split())
    if full and not _is_placeholder_name(full, email_addr, phone):
        return full
    combo = " ".join(
        p for p in [(row.get("first_name", "") or "").strip(), (row.get("last_name", "") or "").strip()] if p
    ).strip()
    if combo and not _is_placeholder_name(combo, email_addr, phone):
        return combo
    first = (row.get("first_name", "") or "").strip()
    if first and not _is_placeholder_name(first, email_addr, phone):
        return first
    return ""


def _row_score(row: Dict[str, Any]) -> tuple:
    phone = _normalize_phone(row.get("phone", ""))
    email_addr = _normalize_email(row.get("email", ""))
    has_phone = bool(phone)
    has_email = bool(email_addr)
    has_non_gateway_email = has_email and not _is_sms_gateway_email(email_addr)
    has_real_name = bool(_best_name(row))
    source_rank = _source_rank(row.get("source", ""))
    updated_at = str(row.get("updated_at") or row.get("discovered_at") or "")
    rid = int(row.get("id") or 0)
    return (
        1 if has_real_name else 0,
        1 if has_phone else 0,
        1 if has_non_gateway_email else 0,
        1 if has_email else 0,
        source_rank,
        updated_at,
        -rid if rid else 0,  # prefer older IDs on tie
    )


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _tables_with_lead_id(conn) -> List[str]:
    tables = [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    out: List[str] = []
    for t in tables:
        if t == "leads":
            continue
        cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
        if any(c["name"] == "lead_id" for c in cols):
            out.append(t)
    return out


def _build_clusters(rows: List[Dict[str, Any]]) -> List[List[int]]:
    if not rows:
        return []
    idx_by_id = {int(r["id"]): i for i, r in enumerate(rows)}
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_phone: Dict[str, int] = {}
    by_email: Dict[str, int] = {}
    for i, row in enumerate(rows):
        phone = _normalize_phone(row.get("phone", ""))
        email_addr = _normalize_email(row.get("email", ""))
        alias_phone = _phone_from_email_alias(email_addr)
        if phone:
            if phone in by_phone:
                union(i, by_phone[phone])
            else:
                by_phone[phone] = i
        if email_addr:
            if email_addr in by_email:
                union(i, by_email[email_addr])
            else:
                by_email[email_addr] = i
        if alias_phone:
            if alias_phone in by_phone:
                union(i, by_phone[alias_phone])
            else:
                by_phone[alias_phone] = i

    groups: Dict[int, List[int]] = {}
    for row in rows:
        rid = int(row["id"])
        root = find(idx_by_id[rid])
        groups.setdefault(root, []).append(rid)
    return [sorted(v) for v in groups.values() if len(v) > 1]


def _merge_read_receipts(conn, canonical_id: int, duplicate_id: int):
    if not _table_exists(conn, "lead_conversation_reads"):
        return
    c_row = conn.execute(
        "SELECT last_read_ts FROM lead_conversation_reads WHERE lead_id = ?",
        (canonical_id,),
    ).fetchone()
    d_row = conn.execute(
        "SELECT last_read_ts FROM lead_conversation_reads WHERE lead_id = ?",
        (duplicate_id,),
    ).fetchone()
    c_ts = (c_row["last_read_ts"] if c_row else "") or ""
    d_ts = (d_row["last_read_ts"] if d_row else "") or ""
    merged_ts = max(c_ts, d_ts)
    if merged_ts:
        conn.execute(
            """
            INSERT INTO lead_conversation_reads (lead_id, last_read_ts, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(lead_id) DO UPDATE SET
                last_read_ts = excluded.last_read_ts,
                updated_at = datetime('now')
            """,
            (canonical_id, merged_ts),
        )
    conn.execute("DELETE FROM lead_conversation_reads WHERE lead_id = ?", (duplicate_id,))


def _append_merge_note(existing_notes: str, canonical_id: int, duplicate_id: int) -> str:
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    marker = f"[identity-merge] merged into lead #{canonical_id} at {stamp} (was #{duplicate_id})"
    base = (existing_notes or "").strip()
    if marker in base:
        return base
    if not base:
        return marker
    return f"{base}\n{marker}"


def _repoint_lead_references(conn, canonical_id: int, duplicate_id: int, tables: List[str]):
    for table in tables:
        if table == "lead_conversation_reads":
            _merge_read_receipts(conn, canonical_id, duplicate_id)
            continue
        conn.execute(
            f"UPDATE {table} SET lead_id = ? WHERE lead_id = ?",
            (canonical_id, duplicate_id),
        )


def _select_canonical(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return max(rows, key=_row_score)


def _apply_canonical_upgrades(conn, canonical: Dict[str, Any], cluster_rows: List[Dict[str, Any]]):
    cid = int(canonical["id"])
    canon_phone = _normalize_phone(canonical.get("phone", ""))
    canon_email = _normalize_email(canonical.get("email", ""))
    canon_name = _best_name(canonical)

    best_non_gateway_email = ""
    best_any_email = ""
    best_phone = ""
    best_name = canon_name
    best_source_row = canonical

    for row in cluster_rows:
        email_addr = _normalize_email(row.get("email", ""))
        phone = _normalize_phone(row.get("phone", "")) or _phone_from_email_alias(email_addr)
        row_name = _best_name(row)
        if email_addr and not best_any_email:
            best_any_email = email_addr
        if email_addr and not _is_sms_gateway_email(email_addr) and not best_non_gateway_email:
            best_non_gateway_email = email_addr
        if phone and not best_phone:
            best_phone = phone
        if row_name and (not best_name or _is_placeholder_name(best_name, canon_email, canon_phone)):
            best_name = row_name
        if _source_rank(row.get("source", "")) > _source_rank(best_source_row.get("source", "")):
            best_source_row = row

    updates: Dict[str, Any] = {}
    if not canon_phone and best_phone:
        updates["phone"] = best_phone
    if (not canon_email) and (best_non_gateway_email or best_any_email):
        updates["email"] = best_non_gateway_email or best_any_email

    if best_name and _is_placeholder_name(canon_name, canonical.get("email", ""), canonical.get("phone", "")):
        first, last = _split_name(best_name)
        updates["first_name"] = first
        updates["last_name"] = last
        updates["full_name"] = f"{first} {last}".strip()

    if _source_rank(best_source_row.get("source", "")) > _source_rank(canonical.get("source", "")):
        updates["source"] = best_source_row.get("source", "")
        if (best_source_row.get("source_detail") or "").strip():
            updates["source_detail"] = best_source_row.get("source_detail", "")
        if (best_source_row.get("source_group_name") or "").strip():
            updates["source_group_name"] = best_source_row.get("source_group_name", "")

    if not updates:
        return
    sets = ", ".join(f"{k} = ?" for k in updates.keys()) + ", updated_at = datetime('now')"
    vals = list(updates.values()) + [cid]
    conn.execute(f"UPDATE leads SET {sets} WHERE id = ?", vals)


def reconcile_identity_cluster(
    conn,
    cluster_ids: List[int],
    tables_with_lead_id: List[str] | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Merge one cluster of duplicate lead IDs."""
    ids = sorted({int(i) for i in cluster_ids if int(i) > 0})
    if len(ids) < 2:
        return {"ok": True, "merged": 0, "canonical_id": ids[0] if ids else 0}

    placeholders = ",".join("?" for _ in ids)
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM leads WHERE id IN ({placeholders})",
        ids,
    ).fetchall()]
    if len(rows) < 2:
        return {"ok": True, "merged": 0, "canonical_id": rows[0]["id"] if rows else 0}

    canonical = _select_canonical(rows)
    canonical_id = int(canonical["id"])
    duplicate_ids = [int(r["id"]) for r in rows if int(r["id"]) != canonical_id]

    if dry_run:
        return {
            "ok": True,
            "canonical_id": canonical_id,
            "merged_ids": duplicate_ids,
            "merged": len(duplicate_ids),
        }

    tables = tables_with_lead_id or _tables_with_lead_id(conn)
    _apply_canonical_upgrades(conn, canonical, rows)

    for dup_id in duplicate_ids:
        _repoint_lead_references(conn, canonical_id, dup_id, tables)
        dup_row = conn.execute("SELECT notes FROM leads WHERE id = ?", (dup_id,)).fetchone()
        merged_note = _append_merge_note((dup_row["notes"] if dup_row else ""), canonical_id, dup_id)
        conn.execute(
            """
            UPDATE leads
            SET status = 'merged_duplicate',
                booking_status = 'archived',
                phone = '',
                email = '',
                notes = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (merged_note[:4000], dup_id),
        )

    conn.execute(
        "INSERT INTO lead_events (ts, lead_id, event_type, details) VALUES (datetime('now'), ?, 'identity_merge', ?)",
        (canonical_id, f"Merged duplicate leads: {', '.join(str(i) for i in duplicate_ids)}"),
    )
    return {
        "ok": True,
        "canonical_id": canonical_id,
        "merged_ids": duplicate_ids,
        "merged": len(duplicate_ids),
    }


def reconcile_lead_identities(
    db_path: Path | None = None,
    limit_clusters: int = 0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Find and merge duplicate lead identities across the full leads table."""
    conn = _conn(db_path)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM leads WHERE COALESCE(status, '') != 'merged_duplicate'"
    ).fetchall()]
    clusters = _build_clusters(rows)
    clusters.sort(key=lambda c: (-len(c), c))
    if limit_clusters and int(limit_clusters) > 0:
        clusters = clusters[:int(limit_clusters)]

    table_cache = _tables_with_lead_id(conn)
    merged_total = 0
    merged_clusters = 0
    details: List[Dict[str, Any]] = []

    for cluster in clusters:
        result = reconcile_identity_cluster(
            conn,
            cluster,
            tables_with_lead_id=table_cache,
            dry_run=dry_run,
        )
        merged = int(result.get("merged") or 0)
        if merged > 0:
            merged_total += merged
            merged_clusters += 1
        details.append(result)

    if dry_run:
        conn.close()
    else:
        conn.commit()
        conn.close()

    return {
        "ok": True,
        "clusters_scanned": len(clusters),
        "clusters_merged": merged_clusters,
        "merged_leads": merged_total,
        "details": details[:100],
    }


def bind_identity_to_lead(
    lead_id: int,
    phone: str = "",
    email: str = "",
    full_name: str = "",
    source: str = "",
    source_detail: str = "",
    db_path: Path | None = None,
    auto_merge: bool = True,
) -> Dict[str, Any]:
    """Attach newly learned identity fields to a lead and reconcile duplicates."""
    conn = _conn(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "lead_not_found", "lead_id": int(lead_id)}

    cur = dict(row)
    norm_phone = _normalize_phone(phone)
    norm_email = _normalize_email(email)
    updates: Dict[str, Any] = {}

    if norm_phone and not _normalize_phone(cur.get("phone", "")):
        updates["phone"] = norm_phone
    if norm_email and not _normalize_email(cur.get("email", "")):
        updates["email"] = norm_email

    display_name = " ".join((full_name or "").strip().split())
    if display_name and _is_placeholder_name(
        _best_name(cur),
        cur.get("email", ""),
        cur.get("phone", ""),
    ):
        first, last = _split_name(display_name)
        updates["first_name"] = first
        updates["last_name"] = last
        updates["full_name"] = f"{first} {last}".strip()

    if source and _source_rank(source) > _source_rank(cur.get("source", "")):
        updates["source"] = source
        if source_detail:
            updates["source_detail"] = source_detail

    if updates:
        sets = ", ".join(f"{k} = ?" for k in updates.keys()) + ", updated_at = datetime('now')"
        vals = list(updates.values()) + [int(lead_id)]
        conn.execute(f"UPDATE leads SET {sets} WHERE id = ?", vals)

    merge_result = {"ok": True, "merged": 0, "canonical_id": int(lead_id)}
    if auto_merge:
        leads = [dict(r) for r in conn.execute(
            "SELECT * FROM leads WHERE COALESCE(status, '') != 'merged_duplicate'"
        ).fetchall()]
        targets: Set[int] = {int(lead_id)}
        if norm_phone:
            for r in leads:
                p = _normalize_phone(r.get("phone", ""))
                alias = _phone_from_email_alias(r.get("email", ""))
                if p == norm_phone or alias == norm_phone:
                    targets.add(int(r["id"]))
        if norm_email:
            alias_phone = _phone_from_email_alias(norm_email)
            for r in leads:
                e = _normalize_email(r.get("email", ""))
                if e == norm_email:
                    targets.add(int(r["id"]))
                if alias_phone:
                    p = _normalize_phone(r.get("phone", ""))
                    alias = _phone_from_email_alias(r.get("email", ""))
                    if p == alias_phone or alias == alias_phone:
                        targets.add(int(r["id"]))
        if len(targets) > 1:
            merge_result = reconcile_identity_cluster(conn, sorted(targets), dry_run=False)

    conn.commit()
    conn.close()
    return {
        "ok": True,
        "lead_id": int(lead_id),
        "updated_fields": sorted(updates.keys()),
        "merge": merge_result,
    }
