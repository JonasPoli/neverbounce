"""
tasks.py
--------
Processamento em background com FastAPI BackgroundTasks.
Suporta paralelismo configurável via ThreadPoolExecutor:
  - Cada e-mail é processado em uma thread separada
  - O número de workers é definido por lista (padrão: 5)
  - Atualizações de progresso são thread-safe via lock
"""

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ListItem, ListStatus
from app.verifier import verify_email
from app.services import cache_service, list_service, settings_service, domain_service
from app.utils import extract_domain

logger = logging.getLogger(__name__)

# Pausa entre batches para não sobrecarregar servidores remotos
BATCH_DELAY_SECONDS = 0.5


def process_list_task(list_id: int):
    """
    Função chamada pelo BackgroundTasks do FastAPI.
    Processa a lista em paralelo usando ThreadPoolExecutor.
    O número de workers é lido do campo EmailList.workers.
    """
    db: Session = SessionLocal()
    lock = threading.Lock()  # Lock para incrementos de contador thread-safe

    try:
        list_service.update_list_status(db, list_id, ListStatus.PROCESSING)
        # Sincroniza contador antes de iniciar para garantir base correta
        list_service.sync_processed_count(db, list_id)
        email_list = list_service.get_list(db, list_id)

        if not email_list:
            logger.error(f"Lista {list_id} não encontrada")
            return

        workers = settings_service.get_workers_count(db)
        workers = max(1, min(workers, 20))  # Clamp: 1–20
        force_check = email_list.force_check

        logger.info(
            f"Processando lista {list_id} com {workers} workers "
            f"({email_list.total_emails} e-mails, force_check={force_check})"
        )

        # Carrega apenas itens pendentes (status IS NULL)
        # Isso evita re-processar itens já finalizados e quebrar o contador processed_count
        items = db.query(ListItem).filter(
            ListItem.list_id == list_id,
            ListItem.status.is_(None)
        ).all()

        def process_item(item: ListItem) -> None:
            """
            Processa um único e-mail em sua própria thread.
            Usa sessão de banco EXCLUSIVA por thread — nunca compartilha a sessão principal.
            """
            thread_db = SessionLocal()
            try:
                # ── Etapa 1: Cache global ────────────────────────────────
                if not force_check:
                    cached = cache_service.get_cached(thread_db, item.email)
                    if cached:
                        _update_item(thread_db, item.id, cached.status, cached.reason or "")
                        logger.debug(f"[cache] {item.email} → {cached.status}")
                        return

                # ── Etapas 2-4: Verificação real ─────────────────────────
                # Respeita o cooldown por domínio antes de contatar o servidor via SMTP
                domain = extract_domain(item.email)
                if domain:
                    domain_service.wait_for_domain_cooldown(thread_db, domain)

                result = verify_email(item.email)
                
                # Salva no cache global e no item da lista
                cache_service.save_to_cache(thread_db, item.email, result)
                _update_item(thread_db, item.id, result)
                logger.debug(f"[verify] {item.email} → {result['status']} (score: {result.get('confidence_score')})")

            except Exception as e:
                logger.error(f"Erro ao processar {item.email}: {e}", exc_info=True)
                try:
                    error_result = {
                        "status": "UNKNOWN",
                        "reason": f"Processing error: {str(e)[:100]}",
                        "technical_status": "PROCESSING_ERROR",
                        "confidence_score": 0
                    }
                    _update_item(thread_db, item.id, error_result)
                except Exception:
                    pass
            finally:
                # Incrementa contador usando a sessão da própria thread
                # O lock evita que duas threads atualizem o contador ao mesmo tempo
                try:
                    with lock:
                        list_service.increment_processed(thread_db, list_id)
                except Exception as e:
                    logger.error(f"Erro ao incrementar contador: {e}", exc_info=True)
                thread_db.close()

        # ── Execução paralela ────────────────────────────────────────
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_item, item) for item in items]

            # Aguarda conclusão e trata exceções não capturadas
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Exceção não tratada em thread: {e}", exc_info=True)

        # ── Finaliza com sucesso ─────────────────────────────────────
        # Sincroniza contador ao final para garantir precisão de 100%
        list_service.sync_processed_count(db, list_id)
        list_service.update_list_status(db, list_id, ListStatus.COMPLETED)
        logger.info(f"Lista {list_id} concluída.")

    except Exception as e:
        logger.error(f"Falha grave ao processar lista {list_id}: {e}", exc_info=True)
        try:
            list_service.update_list_status(db, list_id, ListStatus.FAILED)
        except Exception:
            pass
    finally:
        db.close()


def _update_item(db: Session, item_id: int, result: dict) -> None:
    """Atualiza o status de um ListItem com retry simples para evitar 'database is locked'."""
    from sqlalchemy import text
    import time

    status = result.get("status")
    reason = result.get("reason")
    tech_status = result.get("technical_status")
    score = result.get("confidence_score", 0)
    code = result.get("smtp_code")
    provider = result.get("provider")

    max_retries = 5
    for attempt in range(max_retries):
        try:
            db.execute(
                text(
                    "UPDATE list_items SET status = :status, reason = :reason, "
                    "technical_status = :tech, confidence_score = :score, "
                    "smtp_code = :code, provider = :provider, "
                    "checked_at = :now WHERE id = :id"
                ),
                {
                    "status": status,
                    "reason": reason,
                    "tech": tech_status,
                    "score": score,
                    "code": code,
                    "provider": provider,
                    "now": datetime.utcnow(),
                    "id": item_id
                },
            )
            db.commit()
            return  # Sucesso
        except Exception as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                db.rollback()
                time.sleep(0.1 * (attempt + 1))  # Backoff exponencial simples
                continue
            raise e
