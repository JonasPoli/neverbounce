"""
cache_service.py
----------------
CRUD para a tabela global_cache.
Centraliza leitura e gravação do cache global de e-mails.
TTL granular por classe de resultado.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import GlobalCache, EmailStatus


# ══════════════════════════════════════════════════════════════════════════════
# TTL granular por classe de resultado (em dias)
# ══════════════════════════════════════════════════════════════════════════════

def _get_ttl_days(status: str, technical_failure: bool = False, policy_block: bool = False) -> int:
    """
    TTL por classe de resultado:
    - INVALID definitivo: 30 dias
    - VALID: 15 dias
    - ACCEPT_ALL: 7 dias
    - UNKNOWN com technical_failure: 1 dia
    - UNKNOWN com policy_block: 1 dia
    - UNKNOWN ambíguo: 2 dias
    """
    if status == EmailStatus.INVALID:
        return 30
    if status == EmailStatus.VALID:
        return 15
    if status == EmailStatus.ACCEPT_ALL:
        return 7
    # UNKNOWN
    if technical_failure or policy_block:
        return 1  # Falha temporária: TTL curto
    return 2  # Ambíguo genérico


def get_cached(db: Session, email: str) -> Optional[GlobalCache]:
    """
    Retorna o registro de cache se ele existir e ainda for válido (dentro do TTL).
    """
    entry = db.query(GlobalCache).filter(GlobalCache.email == email).first()
    if not entry:
        return None

    ttl_days = _get_ttl_days(
        entry.status,
        technical_failure=bool(entry.technical_failure),
        policy_block=bool(entry.policy_block),
    )
    expiration_date = entry.last_checked + timedelta(days=ttl_days)
    
    if datetime.utcnow() > expiration_date:
        return None  # Cache expirado

    return entry


def save_to_cache(db: Session, email: str, result: dict) -> GlobalCache:
    """
    Salva ou atualiza o resultado de verificação no cache global.
    """
    existing = db.query(GlobalCache).filter(GlobalCache.email == email).first()

    data = {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "technical_status": result.get("technical_status"),
        "confidence_score": result.get("confidence_score", 0),
        "smtp_code": result.get("smtp_code"),
        "provider": result.get("provider"),
        "normalized_reason": result.get("normalized_reason"),
        "technical_failure": result.get("technical_failure", False),
        "retryable": result.get("retryable", False),
        "policy_block": result.get("policy_block", False),
        "accept_all_score": str(result.get("accept_all_score", 0.0)),
    }

    if existing:
        for key, value in data.items():
            setattr(existing, key, value)
        existing.last_checked = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        entry = GlobalCache(
            email=email,
            last_checked=datetime.utcnow(),
            **data,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry
