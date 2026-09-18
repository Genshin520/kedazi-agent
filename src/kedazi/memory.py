"""持久化 LangGraph Store 最小适配器：仅 get/put/delete。"""

import asyncio
import json
import sqlite3
from datetime import datetime

from langgraph.store.base import BaseStore, GetOp, Item, PutOp


class SQLiteProfileStore(BaseStore):
    def __init__(self, path):
        self.path = str(path)
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS memories (
                namespace TEXT, key TEXT, value TEXT, created TEXT, updated TEXT,
                PRIMARY KEY(namespace, key))""")

    def batch(self, ops):
        results = []
        with sqlite3.connect(self.path, timeout=10) as conn:
            for op in ops:
                if not isinstance(op, (GetOp, PutOp)):
                    raise NotImplementedError("此 Store 仅支持 get/put/delete")
                namespace = json.dumps(op.namespace)
                if isinstance(op, GetOp):
                    row = conn.execute(
                        "SELECT value, created, updated FROM memories WHERE namespace=? AND key=?",
                        (namespace, op.key),
                    ).fetchone()
                    results.append(
                        Item(
                            value=json.loads(row[0]),
                            key=op.key,
                            namespace=op.namespace,
                            created_at=datetime.fromisoformat(row[1]),
                            updated_at=datetime.fromisoformat(row[2]),
                        )
                        if row
                        else None
                    )
                else:
                    if op.value is None:
                        conn.execute(
                            "DELETE FROM memories WHERE namespace=? AND key=?", (namespace, op.key)
                        )
                    else:
                        now = datetime.now().astimezone().isoformat()
                        conn.execute(
                            """INSERT INTO memories VALUES(?,?,?,?,?)
                            ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated=excluded.updated""",
                            (namespace, op.key, json.dumps(op.value, ensure_ascii=False), now, now),
                        )
                    results.append(None)
        return results

    async def abatch(self, ops):
        return await asyncio.to_thread(self.batch, list(ops))
