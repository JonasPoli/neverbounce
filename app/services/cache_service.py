"""
cache_service.py
----------------
CRUD para a tabela global_cache.
Centraliza leitura e gravação do cache global de e-mails.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import GlobalCache


def get_cached(db: Session, email: str) -> Optional[GlobalCache]:
    """Retorna o registro de cache para um e-mail, ou None se não existir."""
    return db.query(GlobalCache).filter(GlobalCache.email == email).first()


def save_to_cache(db: Session, email: str, status: str, reason: str) -> GlobalCache:
    """
    Salva ou atualiza o resultado de verificação no cache global.
    Se já existir, atualiza status, reason e last_checked.
    """
    existing = get_cached(db, email)

    if existing:
        existing.status = status
        existing.reason = reason
        existing.last_checked = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        entry = GlobalCache(
            email=email,
            status=status,
            reason=reason,
            last_checked=datetime.utcnow(),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
