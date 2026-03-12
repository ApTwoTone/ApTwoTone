from __future__ import annotations
"""
Zoar Ad Creative Management System
- Manages ad creatives lifecycle: draft -> testing -> active -> winner/fatigued
- Tracks performance metrics (CPL, CTR, CPM, hook rate, frequency)
- A/B test engine with statistical confidence tracking
- Ad copy library with audience-segmented copy in EN/ES
- Fatigue detection: frequency > 2.5, CPL +20%, CTR -20%
"""
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".nexus" / "memory.db"


class AdEngine:
    """Ad creative lifecycle manager for Zoar Bathroom Rentals.

    Handles creative tracking, fatigue detection, A/B testing,
    and a segmented ad copy library for Meta Ads campaigns.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or DB_PATH)
        self._ensure_tables()

    # ── Database helpers ──────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self) -> None:
        """Create all ad-related tables if they don't exist."""
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ad_creatives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                concept_number INTEGER,
                type TEXT NOT NULL,
                format TEXT,
                status TEXT DEFAULT 'draft',
                primary_text TEXT,
                headline TEXT,
                description TEXT,
                cta TEXT DEFAULT 'Get Quote',
                target_audience TEXT,
                language TEXT DEFAULT 'en',
                media_path TEXT,
                thumbnail_path TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                launched_at TEXT,
                paused_at TEXT,
                impressions INTEGER DEFAULT 0,
                reach INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                leads INTEGER DEFAULT 0,
                cpl REAL,
                ctr REAL,
                cpm REAL,
                hook_rate REAL,
                thumbstop_rate REAL,
                frequency REAL,
                amount_spent REAL DEFAULT 0,
                quality_ranking TEXT,
                engagement_ranking TEXT,
                conversion_ranking TEXT,
                last_metrics_update TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS ad_copy_library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                audience TEXT,
                language TEXT DEFAULT 'en',
                content TEXT NOT NULL,
                performance_score REAL,
                times_used INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                source TEXT
            );

            CREATE TABLE IF NOT EXISTS creative_test_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creative_id INTEGER,
                test_type TEXT,
                variant_a_id INTEGER,
                variant_b_id INTEGER,
                status TEXT DEFAULT 'running',
                start_date TEXT,
                end_date TEXT,
                impressions_a INTEGER DEFAULT 0,
                impressions_b INTEGER DEFAULT 0,
                leads_a INTEGER DEFAULT 0,
                leads_b INTEGER DEFAULT 0,
                cpl_a REAL,
                cpl_b REAL,
                confidence REAL,
                winner_id INTEGER,
                notes TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_ac_status ON ad_creatives(status);
            CREATE INDEX IF NOT EXISTS idx_ac_audience ON ad_creatives(target_audience);
            CREATE INDEX IF NOT EXISTS idx_acl_category ON ad_copy_library(category);
            CREATE INDEX IF NOT EXISTS idx_acl_audience ON ad_copy_library(audience);
            CREATE INDEX IF NOT EXISTS idx_ctl_status ON creative_test_log(status);
        """)
        conn.commit()
        conn.close()

    # ── Creative queries ──────────────────────────────────────────────────

    def get_active_creatives(self) -> list[dict]:
        """Return all creatives with status in ('testing', 'active', 'winner')."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM ad_creatives WHERE status IN ('testing', 'active', 'winner') "
            "ORDER BY leads DESC, impressions DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_winners(self) -> list[dict]:
        """Return all creatives marked as winners, ordered by lowest CPL."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM ad_creatives WHERE status = 'winner' "
            "ORDER BY COALESCE(cpl, 999999) ASC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_creative(self, creative_id: int) -> dict | None:
        """Return a single creative by ID."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM ad_creatives WHERE id = ?", (creative_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # ── Fatigue detection ─────────────────────────────────────────────────

    def check_fatigue(self, creative_id: int) -> dict:
        """Check if a creative is fatigued.

        Fatigue triggers:
        - frequency > 2.5 (audience seeing ad too often)
        - CPL increased 20%+ from best recorded CPL
        - CTR dropped 20%+ from peak recorded CTR

        Returns:
            {'fatigued': bool, 'reasons': list[str], 'creative': dict}
        """
        creative = self.get_creative(creative_id)
        if not creative:
            return {"fatigued": False, "reasons": ["Creative not found"], "creative": None}

        reasons = []
        freq = creative.get("frequency") or 0
        cpl = creative.get("cpl")
        ctr = creative.get("ctr")

        # Frequency check
        if freq > 2.5:
            reasons.append(f"Frequency too high: {freq:.1f} (threshold: 2.5)")

        # CPL degradation — compare to best historical CPL for this audience
        if cpl and cpl > 0:
            conn = self._conn()
            row = conn.execute(
                "SELECT MIN(cpl) FROM ad_creatives "
                "WHERE target_audience = ? AND cpl > 0 AND status IN ('active', 'winner')",
                (creative.get("target_audience"),)
            ).fetchone()
            conn.close()
            best_cpl = row[0] if row and row[0] else None
            if best_cpl and cpl > best_cpl * 1.2:
                reasons.append(
                    f"CPL degraded: ${cpl:.2f} vs best ${best_cpl:.2f} "
                    f"(+{((cpl - best_cpl) / best_cpl) * 100:.0f}%)"
                )

        # CTR drop — compare to peak CTR across all active creatives
        if ctr is not None:
            conn = self._conn()
            row = conn.execute(
                "SELECT MAX(ctr) FROM ad_creatives "
                "WHERE target_audience = ? AND ctr > 0 AND status IN ('active', 'winner')",
                (creative.get("target_audience"),)
            ).fetchone()
            conn.close()
            peak_ctr = row[0] if row and row[0] else None
            if peak_ctr and ctr < peak_ctr * 0.8:
                reasons.append(
                    f"CTR dropped: {ctr:.2f}% vs peak {peak_ctr:.2f}% "
                    f"(-{((peak_ctr - ctr) / peak_ctr) * 100:.0f}%)"
                )

        return {
            "fatigued": len(reasons) > 0,
            "reasons": reasons,
            "creative": creative,
        }

    # ── Creative rotation ─────────────────────────────────────────────────

    def rotate_creative(self, creative_id: int) -> dict:
        """Mark a creative as fatigued and pause it.

        Returns:
            {'rotated': bool, 'creative_id': int, 'new_status': str, 'message': str}
        """
        creative = self.get_creative(creative_id)
        if not creative:
            return {"rotated": False, "creative_id": creative_id,
                    "new_status": None, "message": "Creative not found"}

        now = datetime.utcnow().isoformat(timespec="seconds")
        conn = self._conn()
        conn.execute(
            "UPDATE ad_creatives SET status = 'fatigued', paused_at = ? WHERE id = ?",
            (now, creative_id)
        )
        conn.commit()
        conn.close()

        return {
            "rotated": True,
            "creative_id": creative_id,
            "new_status": "fatigued",
            "message": f"Creative '{creative['name']}' rotated out (fatigued)",
        }

    # ── A/B Testing ───────────────────────────────────────────────────────

    def get_test_results(self) -> list[dict]:
        """Return all A/B tests with their current metrics."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT t.*, "
            "  a.name AS variant_a_name, b.name AS variant_b_name, "
            "  w.name AS winner_name "
            "FROM creative_test_log t "
            "LEFT JOIN ad_creatives a ON t.variant_a_id = a.id "
            "LEFT JOIN ad_creatives b ON t.variant_b_id = b.id "
            "LEFT JOIN ad_creatives w ON t.winner_id = w.id "
            "ORDER BY t.id DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def declare_winner(self, test_id: int) -> dict:
        """Evaluate an A/B test and declare a winner if criteria are met.

        Minimum thresholds:
        - 1000+ impressions per variant
        - 8+ leads per variant
        - 20%+ CPL difference between variants

        Returns:
            {'declared': bool, 'winner_id': int|None, 'reason': str, 'test': dict}
        """
        conn = self._conn()
        test = conn.execute(
            "SELECT * FROM creative_test_log WHERE id = ?", (test_id,)
        ).fetchone()
        if not test:
            conn.close()
            return {"declared": False, "winner_id": None,
                    "reason": "Test not found", "test": None}

        test = dict(test)
        impr_a = test.get("impressions_a") or 0
        impr_b = test.get("impressions_b") or 0
        leads_a = test.get("leads_a") or 0
        leads_b = test.get("leads_b") or 0
        cpl_a = test.get("cpl_a")
        cpl_b = test.get("cpl_b")

        # Check minimum impressions
        if impr_a < 1000 or impr_b < 1000:
            conn.close()
            return {
                "declared": False, "winner_id": None,
                "reason": f"Insufficient impressions: A={impr_a}, B={impr_b} (need 1000+ each)",
                "test": test,
            }

        # Check minimum leads
        if leads_a < 8 or leads_b < 8:
            conn.close()
            return {
                "declared": False, "winner_id": None,
                "reason": f"Insufficient leads: A={leads_a}, B={leads_b} (need 8+ each)",
                "test": test,
            }

        # Check CPL values exist
        if not cpl_a or not cpl_b or cpl_a <= 0 or cpl_b <= 0:
            conn.close()
            return {
                "declared": False, "winner_id": None,
                "reason": "CPL data missing for one or both variants",
                "test": test,
            }

        # Check 20%+ CPL difference
        cpl_diff_pct = abs(cpl_a - cpl_b) / max(cpl_a, cpl_b) * 100
        if cpl_diff_pct < 20:
            conn.close()
            return {
                "declared": False, "winner_id": None,
                "reason": f"CPL difference only {cpl_diff_pct:.1f}% (need 20%+): "
                          f"A=${cpl_a:.2f} vs B=${cpl_b:.2f}",
                "test": test,
            }

        # Declare winner (lower CPL wins)
        winner_id = test["variant_a_id"] if cpl_a < cpl_b else test["variant_b_id"]
        loser_id = test["variant_b_id"] if cpl_a < cpl_b else test["variant_a_id"]
        now = datetime.utcnow().isoformat(timespec="seconds")

        conn.execute(
            "UPDATE creative_test_log SET status = 'completed', winner_id = ?, "
            "end_date = ?, confidence = ? WHERE id = ?",
            (winner_id, now, round(cpl_diff_pct, 1), test_id)
        )
        conn.execute(
            "UPDATE ad_creatives SET status = 'winner' WHERE id = ?",
            (winner_id,)
        )
        conn.execute(
            "UPDATE ad_creatives SET status = 'paused', paused_at = ? WHERE id = ?",
            (now, loser_id)
        )
        conn.commit()
        conn.close()

        winner_label = "A" if winner_id == test["variant_a_id"] else "B"
        return {
            "declared": True,
            "winner_id": winner_id,
            "reason": f"Variant {winner_label} wins — CPL ${min(cpl_a, cpl_b):.2f} vs "
                      f"${max(cpl_a, cpl_b):.2f} ({cpl_diff_pct:.0f}% better)",
            "test": test,
        }

    # ── Metrics ───────────────────────────────────────────────────────────

    def update_metrics(self, creative_id: int, metrics: dict) -> None:
        """Update performance metrics for a creative.

        Args:
            creative_id: The creative to update.
            metrics: Dict with any of: impressions, reach, clicks, leads, cpl,
                     ctr, cpm, hook_rate, thumbstop_rate, frequency,
                     amount_spent, quality_ranking, engagement_ranking,
                     conversion_ranking.
        """
        allowed = {
            "impressions", "reach", "clicks", "leads", "cpl", "ctr", "cpm",
            "hook_rate", "thumbstop_rate", "frequency", "amount_spent",
            "quality_ranking", "engagement_ranking", "conversion_ranking",
        }
        filtered = {k: v for k, v in metrics.items() if k in allowed}
        if not filtered:
            return

        now = datetime.utcnow().isoformat(timespec="seconds")
        filtered["last_metrics_update"] = now

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [creative_id]

        conn = self._conn()
        conn.execute(
            f"UPDATE ad_creatives SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        conn.close()

    # ── Performance report ────────────────────────────────────────────────

    def generate_performance_report(self) -> str:
        """Generate a formatted plain-text report of all active creatives."""
        creatives = self.get_active_creatives()
        if not creatives:
            return "No active creatives found."

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"=== ZOAR AD PERFORMANCE REPORT ===",
            f"Generated: {now}",
            f"Active creatives: {len(creatives)}",
            "",
        ]

        total_spent = 0
        total_leads = 0

        for c in creatives:
            spent = c.get("amount_spent") or 0
            leads = c.get("leads") or 0
            total_spent += spent
            total_leads += leads

            cpl_str = f"${c['cpl']:.2f}" if c.get("cpl") else "N/A"
            ctr_str = f"{c['ctr']:.2f}%" if c.get("ctr") else "N/A"
            freq_str = f"{c['frequency']:.1f}" if c.get("frequency") else "N/A"
            hook_str = f"{c['hook_rate']:.1f}%" if c.get("hook_rate") else "N/A"

            fatigue = self.check_fatigue(c["id"])
            fatigue_flag = " [FATIGUED]" if fatigue["fatigued"] else ""

            lines.append(f"--- #{c['id']} {c['name']}{fatigue_flag} ---")
            lines.append(f"  Status: {c['status']}  |  Type: {c['type']}  |  Audience: {c.get('target_audience', 'N/A')}")
            lines.append(f"  Impressions: {c.get('impressions', 0):,}  |  Reach: {c.get('reach', 0):,}  |  Clicks: {c.get('clicks', 0):,}")
            lines.append(f"  Leads: {leads}  |  CPL: {cpl_str}  |  CTR: {ctr_str}")
            lines.append(f"  Frequency: {freq_str}  |  Hook Rate: {hook_str}  |  Spent: ${spent:,.2f}")

            if fatigue["fatigued"]:
                for reason in fatigue["reasons"]:
                    lines.append(f"  >> {reason}")

            rankings = []
            for r in ("quality_ranking", "engagement_ranking", "conversion_ranking"):
                if c.get(r):
                    label = r.replace("_ranking", "").title()
                    rankings.append(f"{label}: {c[r]}")
            if rankings:
                lines.append(f"  Rankings: {' | '.join(rankings)}")

            lines.append("")

        blended_cpl = f"${total_spent / total_leads:.2f}" if total_leads > 0 else "N/A"
        lines.append(f"=== TOTALS ===")
        lines.append(f"  Total Spent: ${total_spent:,.2f}")
        lines.append(f"  Total Leads: {total_leads}")
        lines.append(f"  Blended CPL: {blended_cpl}")

        return "\n".join(lines)

    # ── Copy library ──────────────────────────────────────────────────────

    def get_copy_for_audience(self, audience: str, category: str | None = None) -> list[dict]:
        """Return ad copy entries for a given audience, optionally filtered by category.

        Args:
            audience: Target audience slug (e.g. 'wedding_en', 'quinceanera_es').
            category: Optional filter: 'primary_text', 'headline', 'description', 'hook', 'cta'.
        """
        conn = self._conn()
        if category:
            rows = conn.execute(
                "SELECT * FROM ad_copy_library WHERE audience = ? AND category = ? "
                "ORDER BY COALESCE(performance_score, 0) DESC",
                (audience, category)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ad_copy_library WHERE audience = ? "
                "ORDER BY category, COALESCE(performance_score, 0) DESC",
                (audience,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Seed data ─────────────────────────────────────────────────────────

    def seed_data(self) -> dict:
        """Seed all 10 creative concepts and 50+ ad copy library entries.

        Safe to call multiple times -- skips if creatives already exist.

        Returns:
            {'creatives_added': int, 'copy_added': int}
        """
        conn = self._conn()
        existing = conn.execute("SELECT COUNT(*) FROM ad_creatives").fetchone()[0]
        if existing > 0:
            conn.close()
            return {"creatives_added": 0, "copy_added": 0, "message": "Already seeded"}

        # ── 10 Creative Concepts ──────────────────────────────────────────

        creatives = [
            # 1. The Reveal
            {
                "name": "The Reveal",
                "concept_number": 1,
                "type": "video",
                "format": "9:16",
                "status": "draft",
                "primary_text": "Your guests walk up expecting a porta-potty... and then they step inside. "
                                "Running water. Air conditioning. Bluetooth speakers. This is Zoar.\n\n"
                                "Starting at $999 for your full event. Book now before summer fills up.",
                "headline": "Your Guests Deserve Better",
                "description": "Luxury restroom trailer rental for weddings and events in SoCal. "
                               "Starting at $999. Flushable toilets, AC, running water.",
                "cta": "Get Quote",
                "target_audience": "wedding_en",
                "language": "en",
                "notes": "15-30 sec video/reel. Hard cut from porta-potty exterior to luxury interior. "
                         "Shock factor opening.",
            },
            # 2. The Quinceañera Queen
            {
                "name": "The Quinceañera Queen",
                "concept_number": 2,
                "type": "carousel",
                "format": "1:1",
                "status": "draft",
                "primary_text": "Tu quinceañera merece lo mejor — y eso incluye los baños.\n\n"
                                "Nuestro trailer de lujo tiene baños con agua corriente, aire acondicionado, "
                                "espejos con luces LED y bocinas Bluetooth.\n\n"
                                "Desde $999 para tu evento completo. Servicio en todo el sur de California.\n\n"
                                "Your quinceañera deserves the best — including the bathrooms. "
                                "Luxury restroom trailer starting at $999.",
                "headline": "Tu quinceañera merece lo mejor",
                "description": "Baños de lujo para quinceañeras y eventos. Desde $999. "
                               "Servicio en Los Angeles, San Fernando Valley, Ventura County.",
                "cta": "Get Quote",
                "target_audience": "quinceanera_es",
                "language": "es",
                "notes": "Bilingual carousel, 5 slides. Slide 1: quinceañera setup. "
                         "Slide 2: interior. Slide 3: features. Slide 4: pricing. Slide 5: CTA.",
            },
            # 3. The Math
            {
                "name": "The Math",
                "concept_number": 3,
                "type": "image",
                "format": "1:1",
                "status": "draft",
                "primary_text": "Wedding DJ: $1,500\nPhotographer: $3,000\nFlowers: $2,000\n"
                                "Venue: $8,000\nLuxury bathrooms your guests will actually talk about: $999\n\n"
                                "The smartest investment at your wedding is the one that keeps "
                                "every single guest comfortable all night long.\n\n"
                                "Zoar Bathroom Rentals. Starting at $999.",
                "headline": "The Smartest $999 You'll Spend",
                "description": "When you see what your guests get — flushable toilets, AC, "
                               "running water, LED lighting — you'll wonder why every event doesn't have one.",
                "cta": "Get Quote",
                "target_audience": "wedding_en",
                "language": "en",
                "notes": "Single image. Price anchoring next to other wedding costs. "
                         "Clean graphic layout with price comparison.",
            },
            # 4. The Horror Story
            {
                "name": "The Horror Story",
                "concept_number": 4,
                "type": "reel",
                "format": "9:16",
                "status": "draft",
                "primary_text": "\"My bridesmaid got stuck in a porta-potty for 20 minutes. "
                                "In July. In her dress.\"\n\n"
                                "Don't let this happen at your wedding.\n\n"
                                "Zoar's luxury restroom trailer has 4 private stalls with "
                                "flushable toilets, running water, AC, and actual mirrors.\n\n"
                                "Starting at $999. Book before it's too late.",
                "headline": "Don't Let This Happen",
                "description": "Porta-potty horror stories are real. Luxury restroom trailer "
                               "rental starting at $999 for your entire event.",
                "cta": "Get Quote",
                "target_audience": "wedding_en",
                "language": "en",
                "notes": "UGC-style reel. Woman tells porta-potty horror story direct to camera. "
                         "Cuts to Zoar interior reveal. 15-30 sec.",
            },
            # 5. The Luxury Walkthrough
            {
                "name": "The Luxury Walkthrough",
                "concept_number": 5,
                "type": "reel",
                "format": "9:16",
                "status": "draft",
                "primary_text": "No talking. No music. Just the sound of running water, "
                                "a door closing, and pure luxury.\n\n"
                                "This is Zoar. Luxury restroom trailer rental for your event.\n\n"
                                "4 private stalls. Flushable toilets. Air conditioning. "
                                "LED lighting. Bluetooth speakers.\n\nStarting at $999.",
                "headline": "Luxury, Delivered",
                "description": "ASMR-style walkthrough of our luxury restroom trailer. "
                               "Every event deserves this.",
                "cta": "Get Quote",
                "target_audience": "general",
                "language": "en",
                "notes": "ASMR reel, 15 sec. Slow-mo interior shots. Sound design: running water, "
                         "soft door close, AC hum. No voiceover.",
            },
            # 6. The Grandma Test
            {
                "name": "The Grandma Test",
                "concept_number": 6,
                "type": "image",
                "format": "1:1",
                "status": "draft",
                "primary_text": "A la izquierda: tu abuela frente a un baño portátil.\n"
                                "A la derecha: tu abuela en nuestro trailer de lujo con aire acondicionado, "
                                "agua corriente y espejos con luces LED.\n\n"
                                "Tu abuela merece lo mejor. Tu evento también.\n\n"
                                "Left: Your abuela facing a porta-potty.\n"
                                "Right: Your abuela in our luxury trailer with AC, running water, and LED mirrors.\n\n"
                                "Zoar Bathroom Rentals. Desde $999 / Starting at $999.",
                "headline": "Your Abuela Deserves Better",
                "description": "Luxury restroom trailer for quinceañeras, weddings, and events. "
                               "Don't make abuela use a porta-potty.",
                "cta": "Get Quote",
                "target_audience": "quinceanera_es",
                "language": "es",
                "notes": "Split image, meme format. Left side: sad/disgusted abuela at porta-potty. "
                         "Right side: happy abuela in luxury trailer. Bilingual copy.",
            },
            # 7. Event Planner Partner
            {
                "name": "Event Planner Partner",
                "concept_number": 7,
                "type": "carousel",
                "format": "1:1",
                "status": "draft",
                "primary_text": "You spend months planning the perfect event. Then guests complain "
                                "about the bathrooms.\n\n"
                                "Partner with Zoar and never apologize for the restrooms again.\n\n"
                                "4-stall luxury trailer. White-glove delivery and setup. "
                                "We handle everything — you take the credit.\n\n"
                                "Event planner rates available. DM for partnership details.",
                "headline": "Stop Apologizing for the Bathrooms",
                "description": "Luxury restroom trailer partnership for event planners. "
                               "White-glove service. We deliver, set up, and pick up. "
                               "You get happy clients.",
                "cta": "Learn More",
                "target_audience": "corporate",
                "language": "en",
                "notes": "B2B carousel for event planners. Slide 1: problem (complaints). "
                         "Slide 2: solution (Zoar). Slide 3: features. Slide 4: partnership perks. "
                         "Slide 5: CTA.",
            },
            # 8. Summer Booking Urgency
            {
                "name": "Summer Booking Urgency",
                "concept_number": 8,
                "type": "image",
                "format": "9:16",
                "status": "draft",
                "primary_text": "June: BOOKED\nJuly 4th weekend: BOOKED\nJuly 12: BOOKED\n"
                                "July 19: AVAILABLE\nJuly 26: BOOKED\nAugust: filling fast...\n\n"
                                "Summer weekends are almost gone. Don't wait until it's too late.\n\n"
                                "Zoar Luxury Bathroom Rentals. Starting at $999.\nBook your date NOW.",
                "headline": "Only 1 Weekend Left",
                "description": "Summer dates are filling up fast. Lock in your luxury restroom "
                               "trailer before they're gone.",
                "cta": "Book Now",
                "target_audience": "general",
                "language": "en",
                "notes": "Stories ad (9:16). Animated calendar with dates being crossed out/marked BOOKED. "
                         "Urgency/scarcity angle. Seasonal — update dates regularly.",
            },
            # 9. The Outdoor Wedding Saver
            {
                "name": "The Outdoor Wedding Saver",
                "concept_number": 9,
                "type": "carousel",
                "format": "1:1",
                "status": "draft",
                "primary_text": "\"We almost didn't rent a luxury bathroom trailer for our outdoor wedding. "
                                "It ended up being the best $999 we spent.\"\n\n"
                                "\"Guests kept telling us how nice the bathrooms were. Some even took selfies "
                                "in there. We're not kidding.\"\n\n"
                                "\"Our photographer actually shot bridal party photos in front of it. "
                                "That's how beautiful it is.\"\n\n"
                                "Join 100+ happy couples. Zoar Bathroom Rentals. Starting at $999.",
                "headline": "Best $999 We Spent",
                "description": "Real couples. Real reviews. See why Zoar is the #1 luxury "
                               "restroom trailer rental in SoCal.",
                "cta": "Get Quote",
                "target_audience": "wedding_en",
                "language": "en",
                "notes": "Testimonial carousel. Slide 1: quote + couple photo. "
                         "Slide 2: quote + interior. Slide 3: quote + exterior at wedding. "
                         "Slide 4: features overview. Slide 5: CTA with social proof.",
            },
            # 10. Side by Side
            {
                "name": "Side by Side",
                "concept_number": 10,
                "type": "reel",
                "format": "9:16",
                "status": "draft",
                "primary_text": "Same event. Same guests. Completely different experience.\n\n"
                                "Left: what they expected.\nRight: what you gave them.\n\n"
                                "Zoar Luxury Bathroom Rentals. 4 private stalls, flushable toilets, "
                                "running water, AC, LED lighting, Bluetooth speakers.\n\n"
                                "Starting at $999. Southern California.",
                "headline": "Same Event. Different Experience.",
                "description": "Split-screen comparison: porta-potty vs Zoar luxury trailer. "
                               "The difference is everything.",
                "cta": "Get Quote",
                "target_audience": "general",
                "language": "en",
                "notes": "Split-screen reel. Left: porta-potty experience (dark, cramped, gross). "
                         "Right: Zoar experience (bright, spacious, luxury). Synced shots. 15-30 sec.",
            },
        ]

        creative_cols = [
            "name", "concept_number", "type", "format", "status", "primary_text",
            "headline", "description", "cta", "target_audience", "language", "notes",
        ]
        placeholders = ", ".join("?" for _ in creative_cols)
        col_names = ", ".join(creative_cols)

        for c in creatives:
            conn.execute(
                f"INSERT INTO ad_creatives ({col_names}) VALUES ({placeholders})",
                tuple(c.get(col, "") for col in creative_cols)
            )

        # ── 50+ Ad Copy Library Entries ───────────────────────────────────

        copy_entries = [
            # ── Primary Text — Wedding EN ─────────────────────────────────
            ("primary_text", "wedding_en", "en",
             "Your guests walk up expecting a porta-potty... and then they step inside. "
             "Running water. Air conditioning. Bluetooth speakers. This is Zoar. "
             "Starting at $999 for your full event.",
             "concept_1"),
            ("primary_text", "wedding_en", "en",
             "Wedding DJ: $1,500. Photographer: $3,000. Flowers: $2,000. Venue: $8,000. "
             "Luxury bathrooms your guests will actually talk about: $999. "
             "The smartest investment at your wedding.",
             "concept_3"),
            ("primary_text", "wedding_en", "en",
             "\"My bridesmaid got stuck in a porta-potty for 20 minutes. In July. In her dress.\" "
             "Don't let this happen at your wedding. Zoar's luxury restroom trailer has 4 private "
             "stalls with flushable toilets, running water, AC, and actual mirrors. Starting at $999.",
             "concept_4"),
            ("primary_text", "wedding_en", "en",
             "\"We almost didn't rent a luxury bathroom trailer for our outdoor wedding. "
             "It ended up being the best $999 we spent.\" Join 100+ happy couples. "
             "Zoar Bathroom Rentals.",
             "concept_9"),
            ("primary_text", "wedding_en", "en",
             "Planning an outdoor wedding? Here's the one thing brides forget until it's too late: "
             "the bathrooms. Your guests will spend 8+ hours at your venue. Give them flushable toilets, "
             "running water, and AC. Not a plastic box in 95-degree heat. Zoar. Starting at $999.",
             "pas_wedding"),
            ("primary_text", "wedding_en", "en",
             "You've spent months choosing the perfect venue, the perfect dress, the perfect flowers. "
             "Don't ruin it with porta-potties. One bad bathroom experience and that's what guests "
             "remember. Zoar luxury restroom trailers. 4 stalls. AC. Running water. From $999.",
             "pas_wedding"),
            ("primary_text", "wedding_en", "en",
             "\"But $999 for bathrooms?\" Your guests will use the restroom 4-6 times during your event. "
             "That's more than they'll use the photo booth. More than they'll visit the dessert table. "
             "Make every visit comfortable. Zoar Bathroom Rentals.",
             "objection_handling"),
            ("primary_text", "wedding_en", "en",
             "Every wedding has a moment guests won't stop talking about. Make sure it's the luxury "
             "bathrooms — not the porta-potty horror story. Zoar delivers 4-stall luxury restroom "
             "trailers across SoCal. Starting at $999. Free delivery and setup.",
             "feature_focused"),

            # ── Primary Text — Quinceañera ES ─────────────────────────────
            ("primary_text", "quinceanera_es", "es",
             "Tu quinceañera merece lo mejor — y eso incluye los baños. Nuestro trailer de lujo "
             "tiene baños con agua corriente, aire acondicionado, espejos con luces LED y bocinas "
             "Bluetooth. Desde $999 para tu evento completo.",
             "concept_2"),
            ("primary_text", "quinceanera_es", "es",
             "A la izquierda: tu abuela frente a un baño portátil. A la derecha: tu abuela en "
             "nuestro trailer de lujo con aire acondicionado, agua corriente y espejos con luces LED. "
             "Tu abuela merece lo mejor. Desde $999.",
             "concept_6"),
            ("primary_text", "quinceanera_es", "es",
             "Imagínate: tu quinceañera perfecta. La música, el vestido, la familia. Y luego... "
             "un baño portátil de plástico. No. Tu evento merece baños de lujo. Zoar Bathroom Rentals. "
             "4 baños privados, agua corriente, aire acondicionado, luces LED. Desde $999.",
             "pas_quince"),
            ("primary_text", "quinceanera_es", "es",
             "Para bodas, quinceañeras y eventos especiales en el sur de California. Nuestro trailer "
             "tiene 4 baños privados con inodoros, lavamanos con agua corriente, aire acondicionado, "
             "y espejos con luces LED. Tu familia merece lo mejor. Desde $999.",
             "feature_quince"),
            ("primary_text", "quinceanera_es", "es",
             "Las fechas de verano se están llenando rápido. No esperes hasta el último momento. "
             "Reserva tu trailer de baños de lujo para tu quinceañera hoy. Zoar. Desde $999. "
             "Servicio en Los Angeles, San Fernando Valley, Ventura County.",
             "urgency_quince"),

            # ── Primary Text — Corporate / B2B ────────────────────────────
            ("primary_text", "corporate", "en",
             "You spend months planning the perfect event. Then guests complain about the bathrooms. "
             "Partner with Zoar and never apologize for the restrooms again. "
             "Event planner rates available. DM for partnership details.",
             "concept_7"),
            ("primary_text", "corporate", "en",
             "Your corporate event reflects your brand. Every detail matters — especially the ones "
             "guests don't expect. Zoar luxury restroom trailers: 4 private stalls, AC, running water, "
             "LED lighting. White-glove delivery and pickup. Starting at $999.",
             "b2b_brand"),
            ("primary_text", "corporate", "en",
             "Event planners: tired of fielding complaints about bathrooms at outdoor events? "
             "Zoar handles everything — delivery, setup, and pickup. You take the credit. "
             "Partnership rates available for repeat bookings.",
             "b2b_partnership"),

            # ── Primary Text — General ────────────────────────────────────
            ("primary_text", "general", "en",
             "No talking. No music. Just the sound of running water, a door closing, and pure luxury. "
             "This is Zoar. Luxury restroom trailer rental for your event. 4 private stalls. "
             "Starting at $999.",
             "concept_5"),
            ("primary_text", "general", "en",
             "Same event. Same guests. Completely different experience. Zoar Luxury Bathroom Rentals. "
             "4 private stalls, flushable toilets, running water, AC, LED lighting, Bluetooth speakers. "
             "Starting at $999. Southern California.",
             "concept_10"),
            ("primary_text", "general", "en",
             "June: BOOKED. July 4th weekend: BOOKED. August: filling fast. "
             "Summer weekends are almost gone. Don't wait until it's too late. "
             "Zoar Luxury Bathroom Rentals. Starting at $999. Book your date NOW.",
             "concept_8"),
            ("primary_text", "general", "en",
             "Porta-potties cost $150. But so does a bad Yelp review mentioning your bathrooms. "
             "For $999 your guests get flushable toilets, running water, AC, and LED mirrors. "
             "Zoar. The upgrade your event actually needs.",
             "price_anchoring"),
            ("primary_text", "general", "en",
             "Think about the last outdoor event you went to. Now think about the bathrooms. "
             "Exactly. Don't do that to your guests. Zoar Bathroom Rentals. Starting at $999.",
             "pas_general"),

            # ── Headlines ─────────────────────────────────────────────────
            ("headline", "wedding_en", "en", "Your Guests Deserve Better", "concept_1"),
            ("headline", "wedding_en", "en", "The Smartest $999 You'll Spend", "concept_3"),
            ("headline", "wedding_en", "en", "Don't Let This Happen", "concept_4"),
            ("headline", "wedding_en", "en", "Best $999 We Spent", "concept_9"),
            ("headline", "wedding_en", "en", "Luxury Bathrooms for Your Big Day", "variation"),
            ("headline", "wedding_en", "en", "Your Outdoor Wedding, Perfected", "variation"),
            ("headline", "wedding_en", "en", "The Detail Guests Remember Most", "variation"),
            ("headline", "quinceanera_es", "es", "Tu quinceañera merece lo mejor", "concept_2"),
            ("headline", "quinceanera_es", "es", "Your Abuela Deserves Better", "concept_6"),
            ("headline", "quinceanera_es", "es", "Baños de Lujo para Tu Evento", "variation"),
            ("headline", "quinceanera_es", "es", "No Más Baños Portátiles", "variation"),
            ("headline", "corporate", "en", "Stop Apologizing for the Bathrooms", "concept_7"),
            ("headline", "corporate", "en", "Impress Every Guest", "variation"),
            ("headline", "corporate", "en", "White-Glove Restroom Service", "variation"),
            ("headline", "general", "en", "Luxury, Delivered", "concept_5"),
            ("headline", "general", "en", "Same Event. Different Experience.", "concept_10"),
            ("headline", "general", "en", "Only 1 Weekend Left", "concept_8"),
            ("headline", "general", "en", "Not Your Average Porta-Potty", "variation"),
            ("headline", "general", "en", "The Bathroom Upgrade", "variation"),

            # ── Descriptions ──────────────────────────────────────────────
            ("description", "wedding_en", "en",
             "Luxury restroom trailer rental for weddings and events in SoCal. "
             "Starting at $999. Flushable toilets, AC, running water.",
             "concept_1"),
            ("description", "wedding_en", "en",
             "When you see what your guests get — flushable toilets, AC, running water, "
             "LED lighting — you'll wonder why every event doesn't have one.",
             "concept_3"),
            ("description", "wedding_en", "en",
             "Porta-potty horror stories are real. Luxury restroom trailer rental "
             "starting at $999 for your entire event.",
             "concept_4"),
            ("description", "wedding_en", "en",
             "Real couples. Real reviews. See why Zoar is the #1 luxury restroom "
             "trailer rental in SoCal.",
             "concept_9"),
            ("description", "quinceanera_es", "es",
             "Baños de lujo para quinceañeras y eventos. Desde $999. "
             "Servicio en Los Angeles, San Fernando Valley, Ventura County.",
             "concept_2"),
            ("description", "quinceanera_es", "es",
             "Luxury restroom trailer for quinceañeras, weddings, and events. "
             "Don't make abuela use a porta-potty.",
             "concept_6"),
            ("description", "corporate", "en",
             "Luxury restroom trailer partnership for event planners. White-glove service. "
             "We deliver, set up, and pick up. You get happy clients.",
             "concept_7"),
            ("description", "general", "en",
             "ASMR-style walkthrough of our luxury restroom trailer. Every event deserves this.",
             "concept_5"),
            ("description", "general", "en",
             "Split-screen comparison: porta-potty vs Zoar luxury trailer. The difference is everything.",
             "concept_10"),
            ("description", "general", "en",
             "Summer dates are filling up fast. Lock in your luxury restroom trailer before they're gone.",
             "concept_8"),

            # ── Hooks (first 3 seconds for video) ─────────────────────────
            ("hook", "wedding_en", "en", "Your guests are about to walk into THIS...", "concept_1"),
            ("hook", "wedding_en", "en", "This bride's porta-potty horror story will make you cringe.", "concept_4"),
            ("hook", "wedding_en", "en", "The one wedding expense no one regrets.", "variation"),
            ("hook", "wedding_en", "en", "POV: Your guests discover the bathrooms.", "variation"),
            ("hook", "wedding_en", "en", "Wait for the inside...", "variation"),
            ("hook", "quinceanera_es", "es", "Espera a ver el interior...", "concept_2"),
            ("hook", "quinceanera_es", "es", "Tu abuela cuando ve el baño portátil...", "concept_6"),
            ("hook", "quinceanera_es", "es", "Lo que tus invitados esperan vs. lo que les das.", "variation"),
            ("hook", "general", "en", "You won't believe this is a portable bathroom.", "concept_5"),
            ("hook", "general", "en", "Left or right? Same event, different bathrooms.", "concept_10"),
            ("hook", "general", "en", "Would you use this bathroom? What about this one?", "variation"),
            ("hook", "general", "en", "Luxury has entered the chat.", "variation"),
            ("hook", "corporate", "en", "How many complaints did you get about the bathrooms last event?", "concept_7"),

            # ── CTAs ──────────────────────────────────────────────────────
            ("cta", "wedding_en", "en", "Get Quote", "default"),
            ("cta", "wedding_en", "en", "Book Your Date", "urgency"),
            ("cta", "wedding_en", "en", "Check Availability", "soft"),
            ("cta", "wedding_en", "en", "See the Trailer", "curiosity"),
            ("cta", "quinceanera_es", "es", "Pide Tu Cotización", "default_es"),
            ("cta", "quinceanera_es", "es", "Reserva Tu Fecha", "urgency_es"),
            ("cta", "quinceanera_es", "es", "Get Quote", "default"),
            ("cta", "corporate", "en", "Learn More", "b2b"),
            ("cta", "corporate", "en", "Partner With Us", "b2b_partnership"),
            ("cta", "corporate", "en", "Request Partnership Info", "b2b_soft"),
            ("cta", "general", "en", "Get Quote", "default"),
            ("cta", "general", "en", "Book Now", "urgency"),
            ("cta", "general", "en", "Check Availability", "soft"),
            ("cta", "general", "en", "See Inside", "curiosity"),
        ]

        for cat, aud, lang, content, source in copy_entries:
            conn.execute(
                "INSERT INTO ad_copy_library (category, audience, language, content, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (cat, aud, lang, content, source)
            )

        conn.commit()

        creatives_count = len(creatives)
        copy_count = len(copy_entries)
        conn.close()

        return {
            "creatives_added": creatives_count,
            "copy_added": copy_count,
            "message": f"Seeded {creatives_count} creatives and {copy_count} copy entries",
        }


# ── Module-level convenience ──────────────────────────────────────────────

_engine: AdEngine | None = None


def get_ad_engine(db_path: str | Path | None = None) -> AdEngine:
    """Return a singleton AdEngine instance."""
    global _engine
    if _engine is None:
        _engine = AdEngine(db_path)
    return _engine
