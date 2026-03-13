"""
main.py
-------
Ponto de entrada da aplicação FastAPI.
Configura rotas, templates Jinja2, arquivos estáticos e inicialização do banco.

Rotas:
  GET  /                        → Dashboard
  GET  /upload                  → Formulário de upload
  POST /upload                  → Recebe texto, CSV ou XLSX
  GET  /lists/{id}              → Detalhes de uma lista
  GET  /lists/{id}/export       → Download CSV
  GET  /api/lists/{id}/progress → Progresso em JSON
"""

import os
import logging
from typing import Optional

from fastapi import (
    FastAPI, Request, Form, File, UploadFile, BackgroundTasks,
    HTTPException, Depends, Query
)
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db, init_db
from app.models import ListStatus, EmailStatus
from app.schemas import ProgressResponse
from app.utils import (
    parse_emails_from_text,
    parse_emails_from_csv,
    parse_emails_from_xlsx,
    deduplicate,
)
from app.tasks import process_list_task
from app.services import list_service, export_service, settings_service
from app.services.list_service import get_stuck_lists

# ──────────────────────────────────────────────
# Configuração de logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Caminhos base
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ──────────────────────────────────────────────
# Instância do FastAPI
# ──────────────────────────────────────────────
app = FastAPI(
    title="Email Validator Pro",
    description="Sistema completo de verificação e validação de e-mails",
    version="1.0.0",
)

# ──────────────────────────────────────────────
# Templates Jinja2
# ──────────────────────────────────────────────
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ──────────────────────────────────────────────
# Arquivos estáticos
# ──────────────────────────────────────────────
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "static")),
    name="static",
)

# ──────────────────────────────────────────────
# Inicialização do banco de dados
# ──────────────────────────────────────────────
@app.on_event("startup")
def startup_event():
    """
    Inicializa o banco e re-enfileira listas interrompidas.
    Se o uvicorn foi morto durante um processamento, as listas voltam
    a rodar automaticamente — o cache global evita retrabalho dos e-mails
    já verificados.
    """
    init_db()
    logger.info("Banco de dados inicializado.")

    # ── Inicializa Configurações Globais ──────────────────────────
    from app.database import SessionLocal as _SL
    _db = _SL()
    try:
        # Garante que 'workers_count' e 'domain_cooldown' existem
        if not settings_service.get_setting(_db, "workers_count"):
            settings_service.set_setting(_db, "workers_count", "5")
        if not settings_service.get_setting(_db, "domain_cooldown"):
            settings_service.set_setting(_db, "domain_cooldown", "1.5")
    finally:
        _db.close()

    # ── Auto-resume de listas interrompidas ─────────────────────────
    from app.models import EmailList, ListItem
    _db = _SL()
    stuck_ids = []  # Coleta IDs ANTES de fechar a sessão (evita DetachedInstanceError)
    try:
        stuck = get_stuck_lists(_db)
        if stuck:
            logger.warning(f"{len(stuck)} lista(s) interrompida(s) — retomando...")
        for lst in stuck:
            # Extrai primitivos enquanto a sessão ainda está aberta
            stuck_ids.append((lst.id, lst.name))
            list_service.update_list_status(_db, lst.id, "PENDING")
            _db.query(EmailList).filter_by(id=lst.id).update({"processed_count": 0})
            _db.query(ListItem).filter(
                ListItem.list_id == lst.id,
                ListItem.status.is_(None),
            ).update({"checked_at": None})
            _db.commit()
    finally:
        _db.close()  # A partir daqui NÃO acessamos mais objetos ORM

    # Dispara threads usando apenas primitivos Python (int, str) — sem ORM
    import threading
    for list_id, list_name in stuck_ids:
        t = threading.Thread(target=process_list_task, args=(list_id,), daemon=True)
        t.start()
        logger.info(f"Lista {list_id} ('{list_name}') retomada em background.")


# ──────────────────────────────────────────────────────────────────────────────
# ROTAS — Dashboard
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    """Página principal com métricas gerais e lista de verificações recentes."""
    lists = list_service.get_all_lists(db, limit=20)
    metrics = list_service.get_dashboard_metrics(db)
    workers_count = settings_service.get_workers_count(db)
    domain_cooldown = settings_service.get_domain_cooldown(db)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request, 
            "lists": lists, 
            "metrics": metrics,
            "workers_count": workers_count,
            "domain_cooldown": domain_cooldown
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# ROTAS — Configurações
# ──────────────────────────────────────────────────────────────────────────────
@app.post("/api/settings/workers", name="update_workers")
def update_workers(workers: int = Form(...), db: Session = Depends(get_db)):
    """Atualiza o número global de workers."""
    workers_clamped = max(1, min(workers, 20))
    settings_service.set_setting(db, "workers_count", str(workers_clamped))
    return JSONResponse({"status": "ok", "workers": workers_clamped})


@app.post("/api/settings/cooldown", name="update_cooldown")
def update_cooldown(cooldown: float = Form(...), db: Session = Depends(get_db)):
    """Atualiza o tempo global de cooldown por domínio."""
    cooldown_clamped = max(0.1, min(cooldown, 5.0))
    settings_service.set_setting(db, "domain_cooldown", str(cooldown_clamped))
    return JSONResponse({"status": "ok", "cooldown": cooldown_clamped})


# ──────────────────────────────────────────────────────────────────────────────
# ROTAS — Upload
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/upload", response_class=HTMLResponse, name="upload_form")
def upload_form(request: Request):
    """Página de formulário de upload."""
    return templates.TemplateResponse("upload.html", {"request": request})


@app.post("/upload", name="upload_submit")
async def upload_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    email_text: str = Form(default=""),
    force_check: bool = Form(default=False),
    csv_file: UploadFile = File(default=None),
    xlsx_file: UploadFile = File(default=None),
):
    """
    Recebe e-mails por texto colado, CSV ou XLSX.
    Cria a lista no banco e dispara o processamento em background.
    """
    emails = []
    list_name = "Paste"

    # ── Processa texto colado ────────────────────────────────────────
    if email_text and email_text.strip():
        emails = parse_emails_from_text(email_text)
        list_name = "Paste"

    # ── Processa arquivo CSV ─────────────────────────────────────────
    elif csv_file and csv_file.filename:
        content = await csv_file.read()
        if not content:
            return templates.TemplateResponse(
                "upload.html",
                {"request": request, "error": "O arquivo CSV enviado está vazio."},
                status_code=400,
            )
        try:
            emails = parse_emails_from_csv(content)
        except ValueError as e:
            return templates.TemplateResponse(
                "upload.html",
                {"request": request, "error": str(e)},
                status_code=400,
            )
        list_name = csv_file.filename

    # ── Processa arquivo XLSX ────────────────────────────────────────
    elif xlsx_file and xlsx_file.filename:
        content = await xlsx_file.read()
        if not content:
            return templates.TemplateResponse(
                "upload.html",
                {"request": request, "error": "O arquivo XLSX enviado está vazio."},
                status_code=400,
            )
        try:
            emails = parse_emails_from_xlsx(content)
        except ValueError as e:
            return templates.TemplateResponse(
                "upload.html",
                {"request": request, "error": str(e)},
                status_code=400,
            )
        list_name = xlsx_file.filename

    else:
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "error": "Nenhum e-mail ou arquivo foi fornecido."},
            status_code=400,
        )

    # ── Deduplicação ────────────────────────────────────────────────
    emails = deduplicate(emails)

    if not emails:
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "error": "Nenhum e-mail válido encontrado na entrada."},
            status_code=400,
        )

    # ── Cria lista no banco ──────────────────────────────────────────
    workers_global = settings_service.get_workers_count(db)
    email_list = list_service.create_list(db, list_name, emails, force_check, workers=workers_global)

    # ── Dispara processamento em background ─────────────────────────
    background_tasks.add_task(process_list_task, email_list.id)

    logger.info(
        f"Lista '{list_name}' criada (id={email_list.id}, "
        f"{len(emails)} e-mails, force_check={force_check}, workers={workers_clamped})"
    )

    # Redireciona para a página de detalhes
    return RedirectResponse(
        url=f"/lists/{email_list.id}",
        status_code=303,
    )


# ──────────────────────────────────────────────────────────────────────────────
# ROTAS — Detalhes da lista
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/lists/{list_id}", response_class=HTMLResponse, name="list_detail")
def list_detail(
    request: Request,
    list_id: int,
    status_filter: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Detalhes de uma lista: gráfico, tabela paginada e botão de exportação."""
    email_list = list_service.get_list(db, list_id)
    if not email_list:
        raise HTTPException(status_code=404, detail="Lista não encontrada")

    per_page = 50
    items, total = list_service.get_list_items(db, list_id, status_filter, page, per_page)
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Estatísticas para o gráfico donut
    from sqlalchemy import func
    from app.models import ListItem
    status_stats = (
        db.query(ListItem.status, func.count(ListItem.id))
        .filter(ListItem.list_id == list_id, ListItem.status.isnot(None))
        .group_by(ListItem.status)
        .all()
    )
    chart_data = {row[0]: row[1] for row in status_stats}

    return templates.TemplateResponse(
        "list_detail.html",
        {
            "request": request,
            "email_list": email_list,
            "items": items,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "status_filter": status_filter,
            "chart_data": chart_data,
            "per_page": per_page,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# ROTAS — Exportação CSV
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/lists/{list_id}/export", name="list_export")
def list_export(list_id: int, db: Session = Depends(get_db)):
    """Gera e retorna o CSV de resultados da lista."""
    email_list = list_service.get_list(db, list_id)
    if not email_list:
        raise HTTPException(status_code=404, detail="Lista não encontrada")

    # Busca todos os itens para exportação (sem paginação)
    from app.models import ListItem
    items = db.query(ListItem).filter(ListItem.list_id == list_id).all()

    filepath = export_service.export_list_to_csv(list_id, email_list.name, items)

    return FileResponse(
        path=filepath,
        filename=os.path.basename(filepath),
        media_type="text/csv",
    )


@app.post("/lists/{list_id}/reprocess-unknown", name="list_reprocess_unknown")
def reprocess_unknown(
    list_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Filtra itens UNKNOWN, reseta o status para None e re-dispara o processamento.
    """
    email_list = list_service.get_list(db, list_id)
    if not email_list:
        raise HTTPException(status_code=404, detail="Lista não encontrada")
    
    # Reseta itens UNKNOWN para None para que sejam processados novamente
    count = list_service.reset_unknown_items(db, list_id)
    
    if count > 0:
        # Atualiza status da lista se necessário
        list_service.update_list_status(db, list_id, ListStatus.PROCESSING)
        
        # Dispara tarefa em background
        background_tasks.add_task(process_list_task, list_id)
        
        logger.info(f"Reprocessamento de {count} UNKNOWNs iniciado para lista {list_id}")
        return RedirectResponse(url=f"/lists/{list_id}", status_code=303)
    
    return RedirectResponse(url=f"/lists/{list_id}", status_code=303)


# ──────────────────────────────────────────────────────────────────────────────
# ROTAS — API de progresso (JSON)
# ──────────────────────────────────────────────────────────────────────────────
@app.get("/api/lists/{list_id}/progress", response_model=ProgressResponse, name="list_progress")
def list_progress(list_id: int, db: Session = Depends(get_db)):
    """Retorna o progresso atual da lista em JSON. Usado pelo frontend para polling."""
    email_list = list_service.get_list(db, list_id)
    if not email_list:
        raise HTTPException(status_code=404, detail="Lista não encontrada")

    percent = 0.0
    if email_list.total_emails > 0:
        percent = round((email_list.processed_count / email_list.total_emails) * 100, 1)

    return ProgressResponse(
        list_id=list_id,
        status=email_list.status,
        total_emails=email_list.total_emails,
        processed_count=email_list.processed_count,
        percent=percent,
    )
