"""
schemas.py
----------
Schemas Pydantic para validação de entrada e resposta da API.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


# ──────────────────────────────────────────────
# Schemas de ListItem
# ──────────────────────────────────────────────
class ListItemOut(BaseModel):
    id: int
    email: str
    status: Optional[str] = None
    reason: Optional[str] = None
    checked_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# Schemas de EmailList
# ──────────────────────────────────────────────
class EmailListOut(BaseModel):
    id: int
    name: str
    total_emails: int
    processed_count: int
    status: str
    force_check: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# Schema de progresso (endpoint /api/lists/{id}/progress)
# ──────────────────────────────────────────────
class ProgressResponse(BaseModel):
    list_id: int
    status: str
    total_emails: int
    processed_count: int
    percent: float


# ──────────────────────────────────────────────
# Schema de resultado de verificação individual
# ──────────────────────────────────────────────
class VerificationResult(BaseModel):
    email: str
    status: str
    reason: str
    from_cache: bool = False
