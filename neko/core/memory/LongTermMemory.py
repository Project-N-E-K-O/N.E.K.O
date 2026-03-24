import sqlite3
import json

class LongTermMemory:
    """
    Long-term memory engine for the N.E.K.O companion.
    Persists user preferences and emotional context across restarts.
    """
    def __init__(self, db_path="neko_memory.db"):
        self.conn = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS memories (key TEXT PRIMARY KEY, value TEXT)")

    def save(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO memories (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        self.conn.commit()

    def load(self, key):
        cur = self.conn.execute("SELECT value FROM memories WHERE key = ?", (key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None
