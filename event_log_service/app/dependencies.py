"""FastAPI dependency functions — injected into route handlers via Depends()."""
from __future__ import annotations

from typing import AsyncGenerator

import asyncpg

from app.database import get_pool


async def get_db_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    """Yield a single connection from the pool for the duration of a request."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
