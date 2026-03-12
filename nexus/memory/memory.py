import sqlite3, json, asyncio
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".nexus" / "memory.db"
DB_PATH.parent.mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT DEFAULT (datetime('now')),
        user_msg TEXT, assistant_msg TEXT, agent_plan TEXT,
        cost_usd REAL DEFAULT 0, duration_sec REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS agent_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT DEFAULT (datetime('now')),
        agent_id TEXT, task_type TEXT, model_used TEXT,
        task TEXT, result TEXT, success INTEGER DEFAULT 1,
        duration_sec REAL DEFAULT 0, error TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS tool_registry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, description TEXT, code TEXT,
        installed_at TEXT DEFAULT (datetime('now')), uses INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS self_improvements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT DEFAULT (datetime('now')),
        weakness TEXT, improvement TEXT, applied INTEGER DEFAULT 0
    );
    """)
    conn.commit()
    return conn

class Memory:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.db = init_db()

    async def save_conversation(self, user_msg, assistant_msg, agent_plan="", cost=0, duration=0):
        async with self._lock:
            self.db.execute(
                "INSERT INTO conversations (user_msg,assistant_msg,agent_plan,cost_usd,duration_sec) VALUES (?,?,?,?,?)",
                (user_msg, assistant_msg, agent_plan, cost, duration))
            self.db.commit()

    async def save_agent_run(self, agent_id, task_type, model, task, result, success=True, duration=0, error=""):
        async with self._lock:
            self.db.execute(
                "INSERT INTO agent_runs (agent_id,task_type,model_used,task,result,success,duration_sec,error) VALUES (?,?,?,?,?,?,?,?)",
                (agent_id, task_type, model, task, result, int(success), duration, error))
            self.db.commit()

    async def get_recent_conversations(self, limit=20):
        rows = self.db.execute("SELECT * FROM conversations ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    async def get_agent_stats(self):
        rows = self.db.execute("""
            SELECT task_type, model_used, COUNT(*) as runs,
                   AVG(duration_sec) as avg_duration,
                   SUM(success)*100.0/COUNT(*) as success_rate
            FROM agent_runs GROUP BY task_type, model_used""").fetchall()
        return [dict(r) for r in rows]

    async def get_weaknesses(self):
        rows = self.db.execute("""
            SELECT task_type, error, COUNT(*) as count
            FROM agent_runs WHERE success=0 AND error!=''
            GROUP BY task_type, error ORDER BY count DESC LIMIT 10""").fetchall()
        return [dict(r) for r in rows]

    async def register_tool(self, name, description, code):
        async with self._lock:
            self.db.execute("INSERT OR REPLACE INTO tool_registry (name,description,code) VALUES (?,?,?)",
                (name, description, code))
            self.db.commit()

    async def export_for_sync(self):
        convs = [dict(r) for r in self.db.execute("SELECT * FROM conversations ORDER BY ts").fetchall()]
        tools = [dict(r) for r in self.db.execute("SELECT * FROM tool_registry").fetchall()]
        return {"conversations":convs,"tools":tools,"exported_at":datetime.utcnow().isoformat()}

    async def import_from_sync(self, data):
        async with self._lock:
            for c in data.get("conversations",[]):
                try:
                    self.db.execute("INSERT OR IGNORE INTO conversations (id,ts,user_msg,assistant_msg,agent_plan) VALUES (?,?,?,?,?)",
                        (c.get("id"),c.get("ts"),c.get("user_msg"),c.get("assistant_msg"),c.get("agent_plan","")))
                except: pass
            self.db.commit()

_memory = None
def get_memory():
    global _memory
    if _memory is None: _memory = Memory()
    return _memory
