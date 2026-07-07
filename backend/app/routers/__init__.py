"""APIルーターの集約点。

後続の Wave は各機能のルーター（rules, jobs, ...）を本パッケージ内に追加し、
ここで `api_router.include_router(...)` するだけでよい（main.py は変更不要）。
"""

from fastapi import APIRouter

from app.routers import ai, artifacts, board, chat, comments, events, tasks

api_router = APIRouter()
api_router.include_router(board.router)
api_router.include_router(tasks.router)
api_router.include_router(comments.router)
api_router.include_router(chat.router)
api_router.include_router(events.router)
api_router.include_router(ai.router)
api_router.include_router(artifacts.router)
