"""
list_service.py
---------------
Serviços de CRUD para EmailList e ListItem.
Centraliza criação, consulta e atualização de listas e seus itens.
"""

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import EmailList, ListItem, ListStatus


# ──────────────────────────────────────────────
# EmailList
# ──────────────────────────────────────────────

def create_list(db: Session, name: str, emails: List[str], force_check: bool, workers: int = 5) -> EmailList:
    """
    Cria uma nova lista com seus itens.
    Salva imediatamente no banco com status PENDING.
    """
    email_list = EmailList(
        name=name,
        total_emails=len(emails),
        processed_count=0,
        status=ListStatus.PENDING,
        force_check=force_check,
        workers=max(1, min(workers, 20)),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(email_list)
    db.flush()  # Garante que email_list.id está disponível

    # Insere os itens em lote
    items = [
        ListItem(list_id=email_list.id, email=email)
        for email in emails
    ]
    db.bulk_save_objects(items)
    db.commit()
    db.refresh(email_list)
    return email_list


def get_stuck_lists(db: Session) -> List[EmailList]:
    """
    Retorna listas que ficaram presas em PROCESSING ou PENDING.
    Chamada no startup do servidor para detectar tarefas interrompidas.
    """
    return (
        db.query(EmailList)
        .filter(EmailList.status.in_([ListStatus.PROCESSING, ListStatus.PENDING]))
        .all()
    )


def get_list(db: Session, list_id: int) -> Optional[EmailList]:
    """Retorna uma lista pelo ID, ou None se não existir."""
    return db.query(EmailList).filter(EmailList.id == list_id).first()


def get_all_lists(db: Session, limit: int = 50) -> List[EmailList]:
    """Retorna todas as listas ordenadas por data de criação decrescente."""
    return (
        db.query(EmailList)
        .order_by(EmailList.created_at.desc())
        .limit(limit)
        .all()
    )


def get_list_items(
    db: Session,
    list_id: int,
    status_filter: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> Tuple[List[ListItem], int]:
    """
    Retorna itens de uma lista com paginação e filtro opcional por status.
    Retorna (itens, total).
    """
    query = db.query(ListItem).filter(ListItem.list_id == list_id)
    if status_filter:
        query = query.filter(ListItem.status == status_filter)
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return items, total


def update_list_status(db: Session, list_id: int, status: str):
    """Atualiza o status de uma lista."""
    db.query(EmailList).filter(EmailList.id == list_id).update(
        {"status": status, "updated_at": datetime.utcnow()}
    )
    db.commit()


def increment_processed(db: Session, list_id: int):
    """Incrementa o contador de e-mails processados de forma segura."""
    from sqlalchemy import text
    db.execute(
        text("UPDATE lists SET processed_count = processed_count + 1, updated_at = :now WHERE id = :id"),
        {"now": datetime.utcnow(), "id": list_id},
    )
    db.commit()


def update_list_item(
    db: Session,
    item: ListItem,
    status: str,
    reason: str,
):
    """Atualiza o resultado de um item da lista."""
    item.status = status
    item.reason = reason
    item.checked_at = datetime.utcnow()
    db.commit()


# ──────────────────────────────────────────────
# Métricas globais para o dashboard
# ──────────────────────────────────────────────

def get_dashboard_metrics(db: Session) -> dict:
    """
    Retorna métricas agregadas para o dashboard:
    - Total de listas
    - Total de e-mails verificados
    - Contagem por status
    - Economia de cache (itens servidos do cache)
    """
    from sqlalchemy import func
    from app.models import GlobalCache

    total_lists = db.query(func.count(EmailList.id)).scalar() or 0
    total_emails = db.query(func.count(ListItem.id)).scalar() or 0

    # Contagem por status nos itens
    status_counts = (
        db.query(ListItem.status, func.count(ListItem.id))
        .filter(ListItem.status.isnot(None))
        .group_by(ListItem.status)
        .all()
    )
    counts = {row[0]: row[1] for row in status_counts}

    # Tamanho do cache global
    cache_size = db.query(func.count(GlobalCache.email)).scalar() or 0

    return {
        "total_lists": total_lists,
        "total_emails": total_emails,
        "valid": counts.get("VALID", 0),
        "invalid": counts.get("INVALID", 0),
        "unknown": counts.get("UNKNOWN", 0),
        "accept_all": counts.get("ACCEPT_ALL", 0),
        "cache_size": cache_size,
    }
