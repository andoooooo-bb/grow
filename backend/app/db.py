"""asyncpg 接続プールヘルパー。

config の DATABASE_URL（postgresql:// スキーム。asyncpg はそのまま受ける）に対する
プロセス共有の接続プールを提供する。アプリ終了時（FastAPI lifespan 等）に
close_pool() を呼んで後始末する。
"""

import asyncpg

from app.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """接続プールを返す（初回呼び出し時に生成し、以後は再利用）。"""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=get_settings().database_url)
    return _pool


async def close_pool() -> None:
    """接続プールを閉じて破棄する（アプリ終了時に呼ぶ）。"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
