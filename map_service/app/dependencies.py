from __future__ import annotations

from typing import AsyncGenerator

import asyncpg

from app.database import get_pool


async def get_db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn