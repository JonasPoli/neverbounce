from datetime import datetime
from sqlalchemy.orm import Session
from app.models import DomainStat
import time
import logging
from typing import Optional
from app.services import settings_service

logger = logging.getLogger(__name__)

def wait_for_domain_cooldown(db: Session, domain: str, cooldown_seconds: float = None):
    """
    Verifica se o domínio está em cooldown. Se estiver, aguarda o tempo necessário.
    Atualiza o timestamp do último contato no banco.
    """
    if cooldown_seconds is None:
        cooldown_seconds = settings_service.get_domain_cooldown(db)
    max_retries = 10
    for attempt in range(max_retries):
        try:
            # Busca status do domínio
            stat = db.query(DomainStat).filter_by(domain=domain).with_for_update().first()
            
            now = datetime.utcnow()
            
            if stat:
                diff = (now - stat.last_contact).total_seconds()
                if diff < cooldown_seconds:
                    wait_time = cooldown_seconds - diff
                    logger.debug(f"Dominio {domain} em cooldown. Aguardando {wait_time:.2f}s")
                    time.sleep(wait_time)
                    # Atualiza o 'now' após a espera
                    now = datetime.utcnow()
                
                stat.last_contact = now
            else:
                # Primeiro contato com este domínio
                stat = DomainStat(domain=domain, last_contact=now)
                db.add(stat)
            
            db.commit()
            return # Cooldown respeitado e registro atualizado
            
        except Exception as e:
            db.rollback()
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
                continue
            logger.warning(f"Erro ao gerenciar cooldown para {domain}: {e}")
            return # Prossegue mesmo com erro para não travar o sistema


def get_accept_all_cache(db: Session, domain: str) -> Optional[bool]:
    """
    Verifica se o domínio está marcado no cache de accept-all (tri-state).
    Retorna:
    - True: se for catch-all confirmado e válido.
    - False: se for confirmado que NÃO é catch-all e válido.
    - None: se não houver informação ou o cache expirou.
    """
    from datetime import timedelta
    stat = db.query(DomainStat).filter_by(domain=domain).first()
    if stat and stat.accept_all_checked_at:
        # TTL de 7 dias para Accept-All
        if datetime.utcnow() < stat.accept_all_checked_at + timedelta(days=7):
            return bool(stat.is_accept_all)
    return None


def check_accept_all_cache(db: Session, domain: str) -> bool:
    """Mantido para compatibilidade, mas prefira get_accept_all_cache."""
    return get_accept_all_cache(db, domain) is True


def set_accept_all(db: Session, domain: str, is_accept_all: bool):
    """Atualiza o status de accept-all para um domínio."""
    stat = db.query(DomainStat).filter_by(domain=domain).first()
    if not stat:
        stat = DomainStat(domain=domain, last_contact=datetime.utcnow())
        db.add(stat)
    
    stat.is_accept_all = is_accept_all
    stat.accept_all_checked_at = datetime.utcnow()
    db.commit()
