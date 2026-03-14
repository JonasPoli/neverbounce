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
    """
    db: Session = SessionLocal()
    lock = threading.Lock()

    try:
        list_service.update_list_status(db, list_id, ListStatus.PROCESSING)
        list_service.sync_processed_count(db, list_id)
        email_list = list_service.get_list(db, list_id)

        if not email_list:
            logger.error(f"Lista {list_id} não encontrada")
            return

        workers = settings_service.get_workers_count(db)
        workers = max(1, min(workers, 20))
        force_check = email_list.force_check

        logger.info(
            f"Processando lista {list_id} com {workers} workers "
            f"({email_list.total_emails} e-mails, force_check={force_check})"
        )

        items = db.query(ListItem).filter(
            ListItem.list_id == list_id,
            ListItem.status.is_(None)
        ).all()

        def process_item(item: ListItem) -> None:
            thread_db = SessionLocal()
            try:
                # ── Etapa 1: Cache global ────────────────────────────
                if not force_check:
                    cached = cache_service.get_cached(thread_db, item.email)
                    if cached:
                        cached_result = {
                            "status": cached.status,
                            "reason": cached.reason or "",
                            "technical_status": cached.technical_status,
                            "confidence_score": cached.confidence_score or 0,
                            "smtp_code": cached.smtp_code,
                            "provider": cached.provider,
                            "normalized_reason": cached.normalized_reason,
                            "technical_failure": bool(cached.technical_failure),
                            "retryable": bool(cached.retryable),
                            "policy_block": bool(cached.policy_block),
                            "accept_all_score": float(cached.accept_all_score or 0),
                        }
                        _update_item(thread_db, item.id, cached_result)
                        logger.debug(f"[cache] {item.email} → {cached.status}")
                        return

                # ── Etapas 2-4: Verificação real ─────────────────────
                domain = extract_domain(item.email)
                if domain:
                    domain_service.wait_for_domain_cooldown(thread_db, domain)

                result = verify_email(item.email)
                
                cache_service.save_to_cache(thread_db, item.email, result)
                _update_item(thread_db, item.id, result)
                logger.debug(
                    f"[verify] {item.email} → {result['status']} "
                    f"(reason={result.get('normalized_reason')}, "
                    f"score={result.get('confidence_score')})"
                )

            except Exception as e:
                logger.error(f"Erro ao processar {item.email}: {e}", exc_info=True)
                try:
                    error_result = {
                        "status": "UNKNOWN",
                        "reason": f"Processing error: {str(e)[:100]}",
                        "normalized_reason": "processing_error",
                        "technical_status": "PROCESSING_ERROR",
                        "confidence_score": 0,
                        "technical_failure": True,
                        "retryable": True,
                        "policy_block": False,
                        "accept_all_score": 0.0,
                    }
                    _update_item(thread_db, item.id, error_result)
                except Exception:
                    pass
            finally:
                try:
                    with lock:
                        list_service.increment_processed(thread_db, list_id)
                except Exception as e:
                    logger.error(f"Erro ao incrementar contador: {e}", exc_info=True)
                thread_db.close()

        # ── Execução paralela ────────────────────────────────────────
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(process_item, item) for item in items]

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Exceção não tratada em thread: {e}", exc_info=True)

        # ── Finaliza com sucesso ─────────────────────────────────────
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
    """Atualiza o status de um ListItem com retry para 'database is locked'."""
    from sqlalchemy import text
    import time

    max_retries = 5
    for attempt in range(max_retries):
        try:
            db.execute(
                text(
                    "UPDATE list_items SET "
                    "status = :status, reason = :reason, "
                    "technical_status = :tech, confidence_score = :score, "
                    "smtp_code = :code, provider = :provider, "
                    "normalized_reason = :norm_reason, "
                    "technical_failure = :tech_fail, retryable = :retryable, "
                    "policy_block = :pol_block, accept_all_score = :aa_score, "
                    "checked_at = :now WHERE id = :id"
                ),
                {
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "tech": result.get("technical_status"),
                    "score": result.get("confidence_score", 0),
                    "code": result.get("smtp_code"),
                    "provider": result.get("provider"),
                    "norm_reason": result.get("normalized_reason"),
                    "tech_fail": result.get("technical_failure", False),
                    "retryable": result.get("retryable", False),
                    "pol_block": result.get("policy_block", False),
                    "aa_score": str(result.get("accept_all_score", 0.0)),
                    "now": datetime.utcnow(),
                    "id": item_id,
                },
            )
            db.commit()
            return
        except Exception as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                db.rollback()
                time.sleep(0.1 * (attempt + 1))
                continue
            raise e
