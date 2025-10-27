# src/api/__init__.py
"""
API package initialization
"""

from .auth import router as auth_router
from .children import router as children_router
from .followups import router as followups_router
from .reports import router as reports_router
from .import_excel import router as import_router

__all__ = [
    "auth_router",
    "children_router",
    "followups_router",
    "reports_router",
    "import_router",
]
