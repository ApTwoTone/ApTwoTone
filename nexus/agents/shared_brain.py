"""
Shared Brain — Central knowledge + communication hub for all Zoar agents.

This isn't just a data store. It's how agents:
1. Share knowledge (insights, metrics, observations)
2. Discuss and brainstorm (post problems, respond with solutions)
3. Track what works and what doesn't (problems, solutions, decisions)
4. Inject cross-agent context into every prompt
5. Adapt behavior based on collective intelligence

Every agent reads the brain before major actions. Every agent writes back
what it learned. The analyst agent synthesizes everything every 2 hours.
"""
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _init_brain_tables():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        -- Main knowledge store
        CREATE TABLE IF NOT EXISTS agent_brain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            source_agent TEXT,
            insight_type TEXT,
            category TEXT,
            content TEXT,
            confidence REAL DEFAULT 0.5,
            data_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_brain_cat ON agent_brain(category);
        CREATE INDEX IF NOT EXISTS idx_brain_type ON agent_brain(insight_type);
        CREATE INDEX IF NOT EXISTS idx_brain_ts ON agent_brain(ts DESC);

        -- Discussion threads — agents post problems, others respond
        CREATE TABLE IF NOT EXISTS agent_discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            thread_id TEXT,
            source_agent TEXT,
            message_type TEXT,   -- 'problem', 'solution', 'question', 'answer', 'idea', 'feedback'
            content TEXT,
            resolved INTEGER DEFAULT 0,
            data_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_disc_thread ON agent_discussions(thread_id);
        CREATE INDEX IF NOT EXISTS idx_disc_unresolved ON agent_discussions(resolved, ts DESC);

        -- Problem/solution knowledge base — learned fixes
        CREATE TABLE IF NOT EXISTS agent_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            source_agent TEXT,
            problem TEXT,
            solution TEXT,
            category TEXT,         -- 'technical', 'strategy', 'content', 'engagement', 'ads'
            times_referenced INTEGER DEFAULT 0,
            data_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_cat ON agent_knowledge(category);

        -- Metrics time series — track CPL, CPC, CTR etc over time
        CREATE TABLE IF NOT EXISTS agent_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            source_agent TEXT,
            metric_name TEXT,
            metric_value REAL,
            category TEXT,
            data_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_name ON agent_metrics(metric_name, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_metrics_cat ON agent_metrics(category);

        -- Agent activity log
        CREATE TABLE IF NOT EXISTS agent_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            agent_type TEXT,
            event_type TEXT,
            details TEXT,
            ts TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_agent_activity_ts ON agent_activity(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_agent_activity_agent ON agent_activity(agent_id);
    """)
    conn.commit()
    conn.close()


_init_brain_tables()


class SharedBrain:
    """Shared knowledge + communication hub for all agents."""

    def __init__(self, agent_id: str = "system"):
        self.agent_id = agent_id

    def _conn(self):
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    # ══════════════════════════════════════════════════════════════════════
    # WRITE — Insights, metrics, observations
    # ══════════════════════════════════════════════════════════════════════

    def write_insight(self, content: str, insight_type: str = "observation",
                      category: str = "general", confidence: float = 0.5,
                      data: dict = None):
        """Write an insight to the shared brain."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_brain (source_agent, insight_type, category, content, confidence, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.agent_id, insight_type, category, content, confidence,
             json.dumps(data or {}))
        )
        conn.commit()
        conn.close()

    def write_metric(self, name: str, value: float, category: str = "performance"):
        """Write a metric data point to BOTH brain and metrics time series."""
        # Brain entry for general visibility
        self.write_insight(
            content=f"{name}: {value}",
            insight_type="metric",
            category=category,
            confidence=1.0,
            data={"metric_name": name, "metric_value": value},
        )
        # Time series entry for trend analysis
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_metrics (source_agent, metric_name, metric_value, category) "
            "VALUES (?, ?, ?, ?)",
            (self.agent_id, name, value, category)
        )
        conn.commit()
        conn.close()

    def write_recommendation(self, content: str, category: str = "general",
                             confidence: float = 0.7):
        """Write a recommendation for other agents to consider."""
        self.write_insight(content, "recommendation", category, confidence)

    def write_decision(self, content: str, category: str = "general",
                       data: dict = None):
        """Record a decision that was made."""
        self.write_insight(content, "decision", category, 1.0, data)

    def log_activity(self, event_type: str, details: str, agent_type: str = ""):
        """Log an activity to the agent_activity table."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_activity (agent_id, agent_type, event_type, details) VALUES (?, ?, ?, ?)",
            (self.agent_id, agent_type or self.agent_id, event_type, details)
        )
        conn.commit()
        conn.close()

    # ══════════════════════════════════════════════════════════════════════
    # DISCUSSION — Cross-agent communication threads
    # ══════════════════════════════════════════════════════════════════════

    def post_problem(self, problem: str, category: str = "general",
                     data: dict = None) -> str:
        """Post a problem for other agents to see and help solve.
        Returns thread_id so responses can be linked."""
        thread_id = f"{self.agent_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_discussions (thread_id, source_agent, message_type, content, data_json) "
            "VALUES (?, ?, 'problem', ?, ?)",
            (thread_id, self.agent_id, problem, json.dumps(data or {"category": category}))
        )
        conn.commit()
        conn.close()
        return thread_id

    def post_solution(self, thread_id: str, solution: str, data: dict = None):
        """Respond to a problem thread with a solution."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_discussions (thread_id, source_agent, message_type, content, data_json) "
            "VALUES (?, ?, 'solution', ?, ?)",
            (thread_id, self.agent_id, solution, json.dumps(data or {}))
        )
        # Mark thread as resolved
        conn.execute(
            "UPDATE agent_discussions SET resolved = 1 WHERE thread_id = ? AND message_type = 'problem'",
            (thread_id,)
        )
        # Get the category from the original problem
        cat_row = conn.execute(
            "SELECT data_json FROM agent_discussions WHERE thread_id = ? AND message_type = 'problem' LIMIT 1",
            (thread_id,)
        ).fetchone()
        category = "general"
        if cat_row:
            try:
                category = json.loads(cat_row[0]).get("category", "general")
            except Exception:
                pass
        # Get the original problem text
        problem_text = self._get_thread_problem(thread_id)
        conn.commit()
        conn.close()
        # Save to knowledge base
        self.save_knowledge(problem=problem_text, solution=solution, category=category)

    def post_idea(self, idea: str, category: str = "general", data: dict = None) -> str:
        """Share an idea for other agents to build on."""
        thread_id = f"idea_{self.agent_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_discussions (thread_id, source_agent, message_type, content, data_json) "
            "VALUES (?, ?, 'idea', ?, ?)",
            (thread_id, self.agent_id, idea, json.dumps(data or {"category": category}))
        )
        conn.commit()
        conn.close()
        return thread_id

    def respond_to_thread(self, thread_id: str, message: str,
                          message_type: str = "feedback"):
        """Add a response to any discussion thread."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_discussions (thread_id, source_agent, message_type, content) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, self.agent_id, message_type, message)
        )
        conn.commit()
        conn.close()

    def get_open_problems(self, limit: int = 10) -> list:
        """Get unresolved problems that need solutions."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM agent_discussions WHERE message_type = 'problem' AND resolved = 0 "
            "ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_thread(self, thread_id: str) -> list:
        """Get all messages in a discussion thread."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM agent_discussions WHERE thread_id = ? ORDER BY ts ASC",
            (thread_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_recent_discussions(self, limit: int = 20) -> list:
        """Get recent discussion entries across all threads."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM agent_discussions ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _get_thread_problem(self, thread_id: str) -> str:
        """Get the original problem from a thread."""
        conn = self._conn()
        row = conn.execute(
            "SELECT content FROM agent_discussions WHERE thread_id = ? AND message_type = 'problem' LIMIT 1",
            (thread_id,)
        ).fetchone()
        conn.close()
        return row[0] if row else ""

    # ══════════════════════════════════════════════════════════════════════
    # KNOWLEDGE BASE — Problems and solutions learned over time
    # ══════════════════════════════════════════════════════════════════════

    def save_knowledge(self, problem: str, solution: str,
                       category: str = "general", data: dict = None):
        """Save a problem/solution pair to the knowledge base."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_knowledge (source_agent, problem, solution, category, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.agent_id, problem, solution, category, json.dumps(data or {}))
        )
        conn.commit()
        conn.close()

    def find_solution(self, problem_keywords: str, limit: int = 5) -> list:
        """Search knowledge base for solutions to similar problems."""
        conn = self._conn()
        # Simple keyword search — match any word
        words = problem_keywords.lower().split()
        results = []
        rows = conn.execute(
            "SELECT * FROM agent_knowledge ORDER BY times_referenced DESC, ts DESC LIMIT 50"
        ).fetchall()
        for row in rows:
            row_dict = dict(row)
            text = (row_dict["problem"] + " " + row_dict["solution"]).lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                row_dict["relevance_score"] = score
                results.append(row_dict)
        conn.close()
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        # Increment reference count for returned solutions
        if results:
            conn = self._conn()
            for r in results[:limit]:
                conn.execute(
                    "UPDATE agent_knowledge SET times_referenced = times_referenced + 1 WHERE id = ?",
                    (r["id"],)
                )
            conn.commit()
            conn.close()
        return results[:limit]

    def get_all_knowledge(self, category: str = None, limit: int = 50) -> list:
        """Get all knowledge entries, optionally filtered."""
        conn = self._conn()
        if category:
            rows = conn.execute(
                "SELECT * FROM agent_knowledge WHERE category = ? ORDER BY times_referenced DESC LIMIT ?",
                (category, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_knowledge ORDER BY times_referenced DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ══════════════════════════════════════════════════════════════════════
    # METRICS TIME SERIES — Track performance over time
    # ══════════════════════════════════════════════════════════════════════

    def record_metric(self, name: str, value: float, category: str = "performance",
                      data: dict = None):
        """Record a metric to the time series (without brain entry)."""
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_metrics (source_agent, metric_name, metric_value, category, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.agent_id, name, value, category, json.dumps(data or {}))
        )
        conn.commit()
        conn.close()

    def get_metric_history(self, metric_name: str, days: int = 7) -> list:
        """Get metric values over time for trend analysis."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM agent_metrics WHERE metric_name = ? AND ts >= ? ORDER BY ts ASC",
            (metric_name, since)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_latest_metrics(self, category: str = None) -> dict:
        """Get the most recent value for each metric."""
        conn = self._conn()
        if category:
            rows = conn.execute(
                "SELECT metric_name, metric_value, ts FROM agent_metrics "
                "WHERE category = ? GROUP BY metric_name "
                "HAVING ts = MAX(ts) ORDER BY ts DESC",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT metric_name, metric_value, ts FROM agent_metrics "
                "GROUP BY metric_name HAVING ts = MAX(ts) ORDER BY ts DESC"
            ).fetchall()
        conn.close()
        return {r[0]: {"value": r[1], "ts": r[2]} for r in rows}

    # ══════════════════════════════════════════════════════════════════════
    # READ — General queries
    # ══════════════════════════════════════════════════════════════════════

    def read_insights(self, category: str = None, insight_type: str = None,
                      limit: int = 20, since: str = None) -> list:
        """Read insights from the brain, optionally filtered."""
        conn = self._conn()
        query = "SELECT * FROM agent_brain WHERE 1=1"
        params = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if insight_type:
            query += " AND insight_type = ?"
            params.append(insight_type)
        if since:
            query += " AND ts >= ?"
            params.append(since)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def read_recommendations(self, category: str = None, limit: int = 10) -> list:
        """Get latest recommendations."""
        return self.read_insights(category=category, insight_type="recommendation", limit=limit)

    def read_metrics(self, category: str = None, limit: int = 20) -> list:
        """Get recent metrics from the brain table."""
        return self.read_insights(category=category, insight_type="metric", limit=limit)

    def get_summary(self) -> dict:
        """Get a high-level summary of the brain's contents."""
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) FROM agent_brain").fetchone()[0]
        by_type = {}
        for row in conn.execute("SELECT insight_type, COUNT(*) FROM agent_brain GROUP BY insight_type"):
            by_type[row[0]] = row[1]
        by_agent = {}
        for row in conn.execute("SELECT source_agent, COUNT(*) FROM agent_brain GROUP BY source_agent"):
            by_agent[row[0]] = row[1]
        by_cat = {}
        for row in conn.execute("SELECT category, COUNT(*) FROM agent_brain GROUP BY category"):
            by_cat[row[0]] = row[1]
        recent = conn.execute(
            "SELECT source_agent, content, ts FROM agent_brain ORDER BY ts DESC LIMIT 5"
        ).fetchall()
        # Discussion stats
        open_problems = conn.execute(
            "SELECT COUNT(*) FROM agent_discussions WHERE message_type = 'problem' AND resolved = 0"
        ).fetchone()[0]
        total_discussions = conn.execute("SELECT COUNT(*) FROM agent_discussions").fetchone()[0]
        knowledge_count = conn.execute("SELECT COUNT(*) FROM agent_knowledge").fetchone()[0]
        conn.close()
        return {
            "total_insights": total,
            "by_type": by_type,
            "by_agent": by_agent,
            "by_category": by_cat,
            "recent": [{"agent": r[0], "content": r[1], "ts": r[2]} for r in recent],
            "open_problems": open_problems,
            "total_discussions": total_discussions,
            "knowledge_entries": knowledge_count,
        }

    # ══════════════════════════════════════════════════════════════════════
    # CROSS-AGENT CONTEXT — What agents inject into their prompts
    # ══════════════════════════════════════════════════════════════════════

    def get_context_for_agent(self, agent_type: str, limit: int = 10) -> str:
        """Get relevant context for an agent to use in its prompts.
        Returns a formatted string of recent insights from OTHER agents,
        plus open problems and recommendations."""
        conn = self._conn()

        # Recent insights from other agents
        insights = conn.execute(
            "SELECT source_agent, insight_type, content, ts FROM agent_brain "
            "WHERE source_agent != ? ORDER BY ts DESC LIMIT ?",
            (agent_type, limit)
        ).fetchall()

        # Open problems any agent posted
        problems = conn.execute(
            "SELECT source_agent, content, ts FROM agent_discussions "
            "WHERE message_type = 'problem' AND resolved = 0 ORDER BY ts DESC LIMIT 5"
        ).fetchall()

        # Recent recommendations
        recs = conn.execute(
            "SELECT source_agent, content FROM agent_brain "
            "WHERE insight_type = 'recommendation' ORDER BY ts DESC LIMIT 5"
        ).fetchall()

        conn.close()

        lines = ["=== SHARED BRAIN CONTEXT ==="]

        if recs:
            lines.append("\n-- RECOMMENDATIONS --")
            for r in recs:
                lines.append(f"  [{r[0]}] {r[1]}")

        if problems:
            lines.append("\n-- OPEN PROBLEMS (help if you can) --")
            for p in problems:
                lines.append(f"  [{p[0]}] {p[1]}")

        if insights:
            lines.append("\n-- RECENT INSIGHTS --")
            for r in insights:
                lines.append(f"  [{r[0]}] ({r[1]}) {r[2]}")

        if len(lines) == 1:
            return "No shared insights yet."

        return "\n".join(lines)

    def get_brainstorm_context(self, topic: str, agents: list = None) -> str:
        """Get a focused context dump for a specific topic.
        Used when agents need to brainstorm together on a topic."""
        conn = self._conn()

        # Search brain for topic-related insights
        words = topic.lower().split()
        all_insights = conn.execute(
            "SELECT source_agent, insight_type, content, confidence, ts FROM agent_brain "
            "ORDER BY ts DESC LIMIT 100"
        ).fetchall()

        relevant = []
        for row in all_insights:
            text = row[2].lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                relevant.append(dict(row))

        # Search knowledge base
        knowledge = conn.execute(
            "SELECT source_agent, problem, solution, category FROM agent_knowledge "
            "ORDER BY times_referenced DESC LIMIT 30"
        ).fetchall()

        relevant_knowledge = []
        for row in knowledge:
            text = (row[1] + " " + row[2]).lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                relevant_knowledge.append(dict(row))

        # Search discussions
        discussions = conn.execute(
            "SELECT source_agent, message_type, content FROM agent_discussions "
            "ORDER BY ts DESC LIMIT 50"
        ).fetchall()

        relevant_disc = []
        for row in discussions:
            if any(w in row[2].lower() for w in words):
                relevant_disc.append(dict(row))

        conn.close()

        lines = [f"=== BRAINSTORM: {topic.upper()} ==="]

        if relevant:
            lines.append(f"\n-- {len(relevant)} related insights --")
            for r in relevant[:10]:
                lines.append(f"  [{r['source_agent']}] {r['content']}")

        if relevant_knowledge:
            lines.append(f"\n-- {len(relevant_knowledge)} known solutions --")
            for k in relevant_knowledge[:5]:
                lines.append(f"  Problem: {k['problem']}")
                lines.append(f"  Solution: {k['solution']}")

        if relevant_disc:
            lines.append(f"\n-- {len(relevant_disc)} discussion entries --")
            for d in relevant_disc[:5]:
                lines.append(f"  [{d['source_agent']}] ({d['message_type']}) {d['content']}")

        return "\n".join(lines)
