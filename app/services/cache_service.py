"""
cache_service.py
----------------
CRUD para a tabela global_cache.
Centraliza leitura e gravação do cache global de e-mails.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import GlobalCache, EmailStatus


# Configuração de TTL por status (em dias)
TTL_CONFIG = {
    EmailStatus.VALID: 15,
    EmailStatus.INVALID: 30,
    EmailStatus.UNKNOWN: 2,
    EmailStatus.ACCEPT_ALL: 7
}


def get_cached(db: Session, email: str) -> Optional[GlobalCache]:
    """
    Retorna o registro de cache se ele existir e ainda for válido (dentro do TTL).
    """
    entry = db.query(GlobalCache).filter(GlobalCache.email == email).first()
    if not entry:
        return None

    # Verifica TTL baseado no status
    ttl_days = TTL_CONFIG.get(entry.status, 7)
    expiration_date = entry.last_checked + timedelta(days=ttl_days)
    
    if datetime.utcnow() > expiration_date:
        return None  # Cache expirado

    return entry


def save_to_cache(db: Session, email: str, result: dict) -> GlobalCache:
    """
    Salva ou atualiza o resultado de verificação no cache global.
    Result deve conter: status, reason, technical_status, confidence_score, smtp_code, provider.
    """
    existing = db.query(GlobalCache).filter(GlobalCache.email == email).first()

    status = result.get("status")
    reason = result.get("reason")
    tech_status = result.get("technical_status")
    score = result.get("confidence_score", 0)
    code = result.get("smtp_code")
    provider = result.get("provider")

    if existing:
        existing.status = status
        existing.reason = reason
        existing.technical_status = tech_status
        existing.confidence_score = score
        existing.smtp_code = code
        existing.provider = provider
        existing.last_checked = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        entry = GlobalCache(
            email=email,
            status=status,
            reason=reason,
            technical_status=tech_status,
            confidence_score=score,
            smtp_code=code,
            provider=provider,
            last_checked=datetime.utcnow(),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
