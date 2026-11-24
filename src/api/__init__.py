"""HTTP layer: FastAPI routers wiring for de²generator.

Import order is irrelevant; ``de2generator.py`` mounts :data:`ROUTERS`.
"""

from . import chat, stats, system, webui

# Mounted in this order: backend API first, then the dashboard and its data.
ROUTERS = (system.router, chat.router, webui.router, stats.router)

__all__ = ["ROUTERS", "chat", "stats", "system", "webui", "paths"]
