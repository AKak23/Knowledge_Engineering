from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from ontology_construct.models import Chunk, LocalOntology


ChunkStatus = Literal["pending", "done", "failed"]


class ProgressTracker:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def register_chunks(self, chunks: list[Chunk]) -> None:
        with self.connection:
            self.connection.executemany(
                """
                INSERT OR IGNORE INTO chunk_progress
                  (chunk_id, status, chunk_json)
                VALUES (?, 'pending', ?)
                """,
                [
                    (
                        chunk.id,
                        json.dumps(chunk.model_dump(), ensure_ascii=False),
                    )
                    for chunk in chunks
                ],
            )

    def is_done(self, chunk_id: str) -> bool:
        row = self.connection.execute(
            "SELECT status FROM chunk_progress WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        return bool(row and row["status"] == "done")

    def pending_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        return [chunk for chunk in chunks if self.status(chunk.id) != "done"]

    def status(self, chunk_id: str) -> ChunkStatus:
        row = self.connection.execute(
            "SELECT status FROM chunk_progress WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if not row:
            return "pending"
        return row["status"]

    def mark_done(self, chunk_id: str, result: LocalOntology) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE chunk_progress
                SET status = 'done',
                    result_json = ?,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE chunk_id = ?
                """,
                (
                    json.dumps(result.model_dump(), ensure_ascii=False),
                    chunk_id,
                ),
            )

    def mark_failed(self, chunk_id: str, error_message: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE chunk_progress
                SET status = 'failed',
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE chunk_id = ?
                """,
                (error_message, chunk_id),
            )

    def counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS count FROM chunk_progress GROUP BY status"
        ).fetchall()
        counts = {"pending": 0, "done": 0, "failed": 0}
        for row in rows:
            counts[row["status"]] = row["count"]
        return counts

    def failed_errors(self, limit: int = 3) -> list[dict[str, str]]:
        rows = self.connection.execute(
            """
            SELECT chunk_id, error_message
            FROM chunk_progress
            WHERE status = 'failed' AND error_message IS NOT NULL
            ORDER BY updated_at, chunk_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {"chunk_id": row["chunk_id"], "error_message": row["error_message"]}
            for row in rows
        ]

    def _init_schema(self) -> None:
        with self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_progress (
                    chunk_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'failed')),
                    chunk_json TEXT NOT NULL,
                    result_json TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
