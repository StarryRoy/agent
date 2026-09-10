"""Durable SQLite checkpointer adapter supporting both Harness call styles."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver


class PersistentSQLiteSaver(BaseCheckpointSaver):
    """Expose sync and async methods over one file-backed official SqliteSaver."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = str(Path(path).resolve())
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.backend = SqliteSaver(self.connection)

    def close(self) -> None:
        self.connection.close()

    def get_tuple(self, *args: Any, **kwargs: Any) -> Any:
        return self.backend.get_tuple(*args, **kwargs)

    def list(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        return self.backend.list(*args, **kwargs)

    def put(self, *args: Any, **kwargs: Any) -> Any:
        return self.backend.put(*args, **kwargs)

    def put_writes(self, *args: Any, **kwargs: Any) -> Any:
        return self.backend.put_writes(*args, **kwargs)

    def delete_thread(self, *args: Any, **kwargs: Any) -> Any:
        return self.backend.delete_thread(*args, **kwargs)

    async def aget_tuple(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.backend.get_tuple, *args, **kwargs)

    async def alist(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        items = await asyncio.to_thread(lambda: list(self.backend.list(*args, **kwargs)))
        for item in items:
            yield item

    async def aput(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.backend.put, *args, **kwargs)

    async def aput_writes(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.backend.put_writes, *args, **kwargs)

    async def adelete_thread(self, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.backend.delete_thread, *args, **kwargs)
