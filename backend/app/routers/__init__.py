"""APIルーターの集約点。

後続の Wave は各機能のルーター（tasks, comments, rules, ...）を本パッケージ内に追加し、
ここで `api_router.include_router(...)` するだけでよい（main.py は変更不要）。
"""

from fastapi import APIRouter

api_router = APIRouter()
