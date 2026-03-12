"""
Lead Scoring System -- Zoar Bathroom Rentals (Nexus Network)

Automatically scores leads based on event type, guest count, timeline,
engagement signals, and responsiveness. Scores are stored directly in the
leads table (lead_score, score_tier, score_updated_at) and organized into
tiers (hot/warm/nurture/cold) for prioritized follow-up.

Database: ~/.nexus/memory.db
Scoring is deterministic and re-runnable. Each call to score_lead()
recalculates from scratch using current lead data + message history.

Tier thresholds:
    hot     >= 80   -- contact immediately
    warm    >= 60   -- active follow-up
    nurture >= 40   -- drip campaign
    cold    <  40   -- monthly check-in
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path.home() / ".nexus" / "memory.db"


# -- Scoring rules ------------------------------------------------------------

SCORING_RULES = {
    # Event type scores (from lead form data)
    "event_type_wedding": 30,
    "event_type_quinceanera": 25,
    "event_type_corporate": 20,
    "event_type_birthday": 15,
    "event_type_baby_shower": 15,
    "event_type_other": 10,

    # Guest count
    "guests_200_plus": 25,
    "guests_100_200": 20,
    "guests_50_100": 15,
    "guests_under_50": 10,

    # Timeline (closer = hotter)
    "event_within_30_days": 30,
    "event_within_60_days": 25,
    "event_within_90_days": 20,
    "event_within_180_days": 15,
    "event_over_180_days": 5,

    # Engagement
    "responded_within_1_hour": 15,
    "responded_within_24_hours": 10,
    "asked_about_price": 10,
    "asked_about_availability": 15,
    "requested_photos": 10,
    "mentioned_deposit": 20,

    # Negative
    "no_response_3_days": -15,
    "no_response_7_days": -25,
    "price_objection": -10,
    "event_over_1_year": -10,
}

SCORE_TIERS = {
    "hot":     (80, "\U0001f525 HOT \u2014 contact immediately"),
    "warm":    (60, "\U0001f7e1 WARM \u2014 active follow-up"),
    "nurture": (40, "\U0001f7e0 NURTURE \u2014 drip campaign"),
    "cold":    (0,  "\u2744\ufe0f COLD \u2014 monthly check-in"),
}


# -- Keyword sets for message scanning ----------------------------------------

_PRICE_KEYWORDS = {
    "price", "pricing", "cost", "how much", "rate", "rates",
    "quote", "estimate", "$", "budget", "1100", "1,100",
}
_AVAILABILITY_KEYWORDS = {
    "available", "availability", "open", "book", "reserve",
    "date", "schedule", "calendar",
}
_PHOTO_KEYWORDS = {
    "photo", "photos", "pictures", "pics", "images", "gallery",
    "see it", "look like", "video",
}
_DEPOSIT_KEYWORDS = {
    "deposit", "down payment", "hold", "secure", "pay", "payment",
    "venmo", "zelle", "card", "invoice",
}
_PRICE_OBJECTION_KEYWORDS = {
    "too much", "expensive", "cheaper", "can't afford", "out of budget",
    "too pricey", "lower price", "discount", "deal",
}


# -- Helpers -------------------------------------------------------------------

def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _get_conn(db_path):
    """Open a connection with row_factory set."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_date(date_str: str):
    """Parse a date string trying multiple common formats. Returns datetime or None."""
    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%B %d, %Y",       # "March 15, 2025"
        "%b %d, %Y",       # "Mar 15, 2025"
        "%m-%d-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _parse_datetime(dt_str: str):
    """Parse a datetime string from the database. Returns datetime or None."""
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ==============================================================================
# LeadScorer
# ==============================================================================

class LeadScorer:
    """
    Score leads based on event data, engagement signals, and responsiveness.

    Usage::

        scorer = LeadScorer()
        result = scorer.score_lead(42)
        # {'lead_id': 42, 'score': 85, 'tier': 'hot', 'tier_label': '...', 'breakdown': [...]}

        report = scorer.format_score_report()  # all-leads summary for Telegram
        report = scorer.format_score_report(lead_id=42)  # single-lead detail
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._ensure_columns()

    # -- Schema ----------------------------------------------------------------

    def _ensure_columns(self):
        """Add lead_score, score_tier, score_updated_at columns to leads table
        if they do not already exist.  Safe to call on every startup."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN lead_score INTEGER DEFAULT 0")
        except Exception:
            pass  # Column already exists
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN score_tier TEXT DEFAULT 'cold'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN score_updated_at TEXT")
        except Exception:
            pass
        conn.commit()
        conn.close()
        print("[LeadScorer] Score columns ensured on leads table")

    # -- Core scoring ----------------------------------------------------------

    def score_lead(self, lead_id: int) -> dict:
        """
        Calculate and persist lead score.

        Returns::

            {
                "lead_id": int,
                "lead_name": str,
                "score": int,          # 0+ (clamped at 0 min)
                "tier": str,           # hot | warm | nurture | cold
                "tier_label": str,     # emoji description
                "breakdown": [         # each rule that fired
                    {"rule": str, "label": str, "points": int},
                    ...
                ],
            }
        """
        conn = _get_conn(self.db_path)
        try:
            lead = conn.execute(
                "SELECT * FROM leads WHERE id = ?", (lead_id,)
            ).fetchone()
            if not lead:
                return {
                    "error": f"Lead {lead_id} not found", "lead_id": lead_id,
                    "score": 0, "tier": "cold",
                    "tier_label": SCORE_TIERS["cold"][1], "breakdown": [],
                }

            ld = dict(lead)
            breakdown = []

            # 1. Event type
            breakdown.extend(self._score_event_type(ld))

            # 2. Guest count
            breakdown.extend(self._score_guest_count(ld))

            # 3. Timeline
            breakdown.extend(self._score_timeline(ld))

            # 4. Engagement (scan messages)
            breakdown.extend(self._score_engagement(conn, lead_id, ld))

            # 5. Responsiveness (reply speed)
            breakdown.extend(self._score_responsiveness(ld))

            # 6. Negative signals
            breakdown.extend(self._score_negative_signals(conn, lead_id, ld))

            total = max(0, sum(item["points"] for item in breakdown))
            # Specialist-first scoring (Runpod-trained lead_ranker), deterministic fallback.
            try:
                from core.specialist_router import get_specialist_router
                routed = get_specialist_router().route("lead_ranker", {"lead": ld, "heuristic_score": total})
                rs_raw = str(routed.get("score", "")).strip()
                rs = float(rs_raw) if rs_raw else None
                source = str(routed.get("source", "")).strip().lower()
                if rs is not None and source and source != "fallback":
                    specialist_score = int(round(rs * 10)) if rs <= 10 else int(round(rs))
                    specialist_score = max(0, min(100, specialist_score))
                    breakdown.append(
                        {
                            "rule": "specialist_lead_ranker_override",
                            "label": f"Specialist override ({total}->{specialist_score})",
                            "points": specialist_score - total,
                        }
                    )
                    total = specialist_score
            except Exception:
                pass
            tier_name, tier_label = self.get_tier(total)

            # Persist score
            conn_w = sqlite3.connect(str(self.db_path))
            try:
                conn_w.execute(
                    "UPDATE leads SET lead_score = ?, score_tier = ?, "
                    "score_updated_at = ? WHERE id = ?",
                    (total, tier_name, _now(), lead_id),
                )
                conn_w.commit()
            finally:
                conn_w.close()

            name = ld.get("first_name") or ld.get("full_name") or f"Lead #{lead_id}"
            print(f"[LeadScorer] Lead #{lead_id} ({name}): score={total} tier={tier_name}")

            return {
                "lead_id": lead_id,
                "lead_name": name,
                "score": total,
                "tier": tier_name,
                "tier_label": tier_label,
                "breakdown": breakdown,
            }

        except Exception as e:
            print(f"[LeadScorer] Error scoring lead #{lead_id}: {e}")
            return {
                "error": str(e), "lead_id": lead_id,
                "score": 0, "tier": "cold",
                "tier_label": SCORE_TIERS["cold"][1], "breakdown": [],
            }
        finally:
            conn.close()

    # -- Individual scoring functions ------------------------------------------

    def _score_event_type(self, lead: dict) -> list[dict]:
        """Score based on event_type field."""
        event_type = (lead.get("event_type") or "").strip().lower()
        if not event_type:
            return []

        mapping = {
            "wedding":        ("event_type_wedding",       "Wedding event"),
            "boda":           ("event_type_wedding",       "Wedding event (boda)"),
            "quinceanera":    ("event_type_quinceanera",   "Quincea\u00f1era event"),
            "quincea\u00f1era": ("event_type_quinceanera", "Quincea\u00f1era event"),
            "quince":         ("event_type_quinceanera",   "Quincea\u00f1era event"),
            "xv":             ("event_type_quinceanera",   "Quincea\u00f1era event"),
            "corporate":      ("event_type_corporate",     "Corporate event"),
            "company":        ("event_type_corporate",     "Corporate event"),
            "gala":           ("event_type_corporate",     "Corporate gala"),
            "birthday":       ("event_type_birthday",      "Birthday party"),
            "baby shower":    ("event_type_baby_shower",   "Baby shower"),
            "baby_shower":    ("event_type_baby_shower",   "Baby shower"),
        }

        for keyword, (rule_key, label) in mapping.items():
            if keyword in event_type:
                return [{"rule": rule_key, "label": label,
                         "points": SCORING_RULES[rule_key]}]

        return [{"rule": "event_type_other",
                 "label": f"Event type: {event_type}",
                 "points": SCORING_RULES["event_type_other"]}]

    def _score_guest_count(self, lead: dict) -> list[dict]:
        """Score based on guest_count field."""
        try:
            guests = int(lead.get("guest_count") or 0)
        except (ValueError, TypeError):
            guests = 0

        if guests <= 0:
            return []

        if guests >= 200:
            return [{"rule": "guests_200_plus",
                     "label": f"{guests} guests (200+)",
                     "points": SCORING_RULES["guests_200_plus"]}]
        if guests >= 100:
            return [{"rule": "guests_100_200",
                     "label": f"{guests} guests (100-200)",
                     "points": SCORING_RULES["guests_100_200"]}]
        if guests >= 50:
            return [{"rule": "guests_50_100",
                     "label": f"{guests} guests (50-100)",
                     "points": SCORING_RULES["guests_50_100"]}]
        return [{"rule": "guests_under_50",
                 "label": f"{guests} guests (<50)",
                 "points": SCORING_RULES["guests_under_50"]}]

    def _score_timeline(self, lead: dict) -> list[dict]:
        """Score based on how soon the event is."""
        event_date_str = (lead.get("event_date") or "").strip()
        if not event_date_str:
            return []

        event_date = _parse_date(event_date_str)
        if not event_date:
            return []

        now = datetime.utcnow()
        days_until = (event_date - now).days

        if days_until < 0:
            return [{"rule": "event_over_1_year",
                     "label": "Event date has passed",
                     "points": SCORING_RULES["event_over_1_year"]}]
        if days_until <= 30:
            return [{"rule": "event_within_30_days",
                     "label": f"Event in {days_until} days",
                     "points": SCORING_RULES["event_within_30_days"]}]
        if days_until <= 60:
            return [{"rule": "event_within_60_days",
                     "label": f"Event in {days_until} days",
                     "points": SCORING_RULES["event_within_60_days"]}]
        if days_until <= 90:
            return [{"rule": "event_within_90_days",
                     "label": f"Event in {days_until} days",
                     "points": SCORING_RULES["event_within_90_days"]}]
        if days_until <= 180:
            return [{"rule": "event_within_180_days",
                     "label": f"Event in {days_until} days",
                     "points": SCORING_RULES["event_within_180_days"]}]
        if days_until <= 365:
            return [{"rule": "event_over_180_days",
                     "label": f"Event in {days_until} days",
                     "points": SCORING_RULES["event_over_180_days"]}]
        return [{"rule": "event_over_1_year",
                 "label": f"Event in {days_until} days (>1 year)",
                 "points": SCORING_RULES["event_over_1_year"]}]

    def _score_engagement(self, conn, lead_id: int, lead: dict) -> list[dict]:
        """Score based on inbound message content (what the lead said)."""
        results = []

        # Fetch all inbound messages
        try:
            messages = conn.execute(
                "SELECT content FROM lead_messages "
                "WHERE lead_id = ? AND direction = 'inbound'",
                (lead_id,),
            ).fetchall()
        except Exception:
            messages = []

        all_text = " ".join((row["content"] or "").lower() for row in messages)

        # Also check notes fields
        notes = (lead.get("notes") or "").lower()
        internal_notes = (lead.get("internal_notes") or "").lower()
        combined = f"{all_text} {notes} {internal_notes}"

        if not combined.strip():
            return []

        if any(kw in combined for kw in _PRICE_KEYWORDS):
            results.append({"rule": "asked_about_price",
                           "label": "Asked about pricing",
                           "points": SCORING_RULES["asked_about_price"]})

        if any(kw in combined for kw in _AVAILABILITY_KEYWORDS):
            results.append({"rule": "asked_about_availability",
                           "label": "Asked about availability",
                           "points": SCORING_RULES["asked_about_availability"]})

        if any(kw in combined for kw in _PHOTO_KEYWORDS):
            results.append({"rule": "requested_photos",
                           "label": "Requested photos",
                           "points": SCORING_RULES["requested_photos"]})

        if any(kw in combined for kw in _DEPOSIT_KEYWORDS):
            results.append({"rule": "mentioned_deposit",
                           "label": "Mentioned deposit/payment",
                           "points": SCORING_RULES["mentioned_deposit"]})

        return results

    def _score_responsiveness(self, lead: dict) -> list[dict]:
        """Score based on how quickly the lead responded after being contacted."""
        last_contacted = lead.get("last_contacted_at") or ""
        last_reply = lead.get("last_reply_at") or ""

        if not last_contacted or not last_reply:
            return []

        contacted_dt = _parse_datetime(last_contacted)
        reply_dt = _parse_datetime(last_reply)

        if not contacted_dt or not reply_dt or reply_dt <= contacted_dt:
            return []

        hours_diff = (reply_dt - contacted_dt).total_seconds() / 3600

        if hours_diff <= 1:
            return [{"rule": "responded_within_1_hour",
                     "label": "Responded within 1 hour",
                     "points": SCORING_RULES["responded_within_1_hour"]}]
        if hours_diff <= 24:
            return [{"rule": "responded_within_24_hours",
                     "label": "Responded within 24 hours",
                     "points": SCORING_RULES["responded_within_24_hours"]}]
        return []

    def _score_negative_signals(self, conn, lead_id: int, lead: dict) -> list[dict]:
        """Score negative signals: no response, price objections."""
        results = []

        last_contacted = lead.get("last_contacted_at") or ""
        last_reply = lead.get("last_reply_at") or ""
        status = lead.get("status") or ""

        # No response penalty
        if last_contacted and not last_reply and status not in (
            "new", "opted_out", "closed"
        ):
            contacted_dt = _parse_datetime(last_contacted)
            if contacted_dt:
                days_since = (datetime.utcnow() - contacted_dt).days
                if days_since >= 7:
                    results.append({
                        "rule": "no_response_7_days",
                        "label": f"No response for {days_since} days",
                        "points": SCORING_RULES["no_response_7_days"],
                    })
                elif days_since >= 3:
                    results.append({
                        "rule": "no_response_3_days",
                        "label": f"No response for {days_since} days",
                        "points": SCORING_RULES["no_response_3_days"],
                    })

        # Price objection in messages
        try:
            messages = conn.execute(
                "SELECT content FROM lead_messages "
                "WHERE lead_id = ? AND direction = 'inbound'",
                (lead_id,),
            ).fetchall()
        except Exception:
            messages = []

        all_text = " ".join((row["content"] or "").lower() for row in messages)
        notes = (lead.get("notes") or "").lower()
        combined = f"{all_text} {notes}"

        if any(kw in combined for kw in _PRICE_OBJECTION_KEYWORDS):
            results.append({"rule": "price_objection",
                           "label": "Price objection detected",
                           "points": SCORING_RULES["price_objection"]})

        return results

    # -- Tier helpers ----------------------------------------------------------

    def get_tier(self, score: int) -> tuple:
        """Return ``(tier_name, emoji_description)`` for a given score.

        Thresholds: hot >= 80, warm >= 60, nurture >= 40, cold < 40.
        """
        for tier_name in ("hot", "warm", "nurture", "cold"):
            threshold, label = SCORE_TIERS[tier_name]
            if score >= threshold:
                return (tier_name, label)
        return ("cold", SCORE_TIERS["cold"][1])

    def get_score_breakdown(self, lead_id: int) -> dict:
        """Show which rules contributed to the score.
        Equivalent to ``score_lead`` (recalculates and persists)."""
        return self.score_lead(lead_id)

    # -- Batch operations ------------------------------------------------------

    def get_leads_by_tier(self, tier: str = None) -> list[dict]:
        """Get leads sorted by score desc, optionally filtered by tier.
        Excludes closed / opted_out leads."""
        conn = _get_conn(self.db_path)
        try:
            base = (
                "SELECT id, first_name, last_name, phone, email, source, status, "
                "booking_status, event_type, event_date, guest_count, "
                "lead_score, score_tier, score_updated_at "
                "FROM leads WHERE status NOT IN ('closed', 'opted_out') "
            )
            if tier:
                rows = conn.execute(
                    base + "AND score_tier = ? ORDER BY lead_score DESC",
                    (tier,),
                ).fetchall()
            else:
                rows = conn.execute(
                    base + "ORDER BY lead_score DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[LeadScorer] Error getting leads by tier: {e}")
            return []
        finally:
            conn.close()

    def update_all_scores(self) -> dict:
        """Recalculate all active lead scores.

        Returns::

            {
                "total": int,
                "scored": int,
                "tiers": {"hot": N, "warm": N, "nurture": N, "cold": N},
                "errors": int,
            }
        """
        conn = _get_conn(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id FROM leads WHERE status NOT IN ('closed', 'opted_out')"
            ).fetchall()
        finally:
            conn.close()

        lead_ids = [row["id"] for row in rows]
        tier_counts = {"hot": 0, "warm": 0, "nurture": 0, "cold": 0}
        error_count = 0

        for lid in lead_ids:
            result = self.score_lead(lid)
            if result.get("error") and not result.get("breakdown"):
                error_count += 1
            else:
                tier = result.get("tier", "cold")
                tier_counts[tier] = tier_counts.get(tier, 0) + 1

        summary = {
            "total": len(lead_ids),
            "scored": len(lead_ids) - error_count,
            "tiers": tier_counts,
            "errors": error_count,
        }
        print(
            f"[LeadScorer] Scored {summary['scored']}/{summary['total']} leads: "
            f"hot={tier_counts['hot']} warm={tier_counts['warm']} "
            f"nurture={tier_counts['nurture']} cold={tier_counts['cold']}"
        )
        return summary

    # -- Formatted output (Telegram) -------------------------------------------

    def format_score_report(self, lead_id: int = None) -> str:
        """Formatted report for Telegram display.

        If *lead_id* is given, show detailed breakdown for that lead.
        Otherwise, refresh all scores and show a tier summary.
        """
        if lead_id:
            return self._format_single_lead(lead_id)
        return self._format_all_leads()

    def _format_single_lead(self, lead_id: int) -> str:
        result = self.score_lead(lead_id)
        if result.get("error") and not result.get("breakdown"):
            return f"Error scoring lead #{lead_id}: {result['error']}"

        name = result.get("lead_name", f"Lead #{lead_id}")
        lines = [
            f"\U0001f4ca Score Report: {name}",
            "=" * 30,
            f"Score: {result['score']} | {result['tier_label']}",
            "",
            "Breakdown:",
        ]

        for item in result["breakdown"]:
            sign = "+" if item["points"] > 0 else ""
            lines.append(f"  {sign}{item['points']:>3}  {item['label']}")

        if not result["breakdown"]:
            lines.append("  (no scoring signals detected)")

        lines.append("")
        lines.append("=" * 30)
        lines.append(f"Total: {result['score']} pts \u2192 {result['tier'].upper()}")
        return "\n".join(lines)

    def _format_all_leads(self) -> str:
        summary = self.update_all_scores()

        lines = [
            "\U0001f4ca Lead Score Report",
            "=" * 35,
        ]

        for tier_name in ("hot", "warm", "nurture", "cold"):
            _, tier_label = SCORE_TIERS[tier_name]
            count = summary["tiers"].get(tier_name, 0)
            lines.append(f"\n{tier_label}: {count} leads")

            leads = self.get_leads_by_tier(tier_name)
            for lead in leads[:10]:
                name = lead.get("first_name") or "Unknown"
                score = lead.get("lead_score", 0)
                event = lead.get("event_type") or "?"
                date = lead.get("event_date") or "no date"
                lines.append(
                    f"  \u2022 {name} ({score}pts) \u2014 {event}, {date}"
                )
            if len(leads) > 10:
                lines.append(f"  ... and {len(leads) - 10} more")

        lines.append("")
        lines.append("=" * 35)
        lines.append(
            f"Total: {summary['total']} active leads | "
            f"{summary['errors']} errors"
        )
        return "\n".join(lines)


# -- Module-level convenience --------------------------------------------------

_scorer = None


def get_scorer(db_path=None) -> LeadScorer:
    """Get or create singleton LeadScorer instance."""
    global _scorer
    if _scorer is None:
        _scorer = LeadScorer(db_path)
    return _scorer
