from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.memory import MemorySaver


def to_psycopg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@asynccontextmanager
async def open_checkpointer(mode: str, database_url: str) -> AsyncIterator[Any]:
    """默认使用内存；显式开启 postgres 时创建并初始化 LangGraph checkpoint 表。"""

    if mode.lower() != "postgres":
        yield MemorySaver()
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(to_psycopg_dsn(database_url)) as saver:
        await saver.setup()
        yield saver
