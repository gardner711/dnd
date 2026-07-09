from __future__ import annotations
from typing import AsyncGenerator
import asyncpg
from app.database import get_pool


async def get_db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Yield a single database connection from the pool for one request."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
