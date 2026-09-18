import json
import uuid

import aiosqlite


class Repository:
    def __init__(self, connection):
        self.db = connection

    @classmethod
    async def open(cls, path):
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS threads(
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
                created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS uploads(
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, object_key TEXT NOT NULL,
                created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS turns(
                id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
                question TEXT NOT NULL, result TEXT NOT NULL,
                created TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        await db.commit()
        return cls(db)

    async def create_thread(self):
        identifier = str(uuid.uuid4())
        await self.db.execute(
            "INSERT INTO threads(id,user_id,title) VALUES(?,?,?)",
            (identifier, "local-student", "新的概率论讨论"),
        )
        await self.db.commit()
        return identifier

    async def thread_exists(self, identifier):
        cursor = await self.db.execute(
            "SELECT 1 FROM threads WHERE id=?", (identifier,)
        )
        return await cursor.fetchone() is not None

    async def list_threads(self):
        cursor = await self.db.execute(
            "SELECT id,title,created FROM threads ORDER BY created DESC,rowid DESC",
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def history(self, identifier):
        cursor = await self.db.execute(
            "SELECT question,result,created FROM turns WHERE thread_id=? ORDER BY id", (identifier,)
        )
        return [
            {"question": row["question"], "result": json.loads(row["result"]), "created": row["created"]}
            for row in await cursor.fetchall()
        ]

    async def save_turn(self, identifier, question, result):
        await self.db.execute(
            "INSERT INTO turns(thread_id,question,result) VALUES(?,?,?)",
            (identifier, question, json.dumps(result, ensure_ascii=False)),
        )
        await self.db.execute(
            "UPDATE threads SET title=? WHERE id=? AND title=?",
            (question[:28], identifier, "新的概率论讨论"),
        )
        await self.db.commit()

    async def add_upload(self, key):
        identifier = str(uuid.uuid4())
        await self.db.execute(
            "INSERT INTO uploads(id,user_id,object_key) VALUES(?,?,?)", (identifier, "local-student", key)
        )
        await self.db.commit()
        return identifier

    async def upload_key(self, identifier):
        cursor = await self.db.execute(
            "SELECT object_key FROM uploads WHERE id=?", (identifier,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None
