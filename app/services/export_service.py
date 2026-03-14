"""
export_service.py
-----------------
Geração de arquivos CSV para exportação de resultados.
Salva os arquivos no diretório /exports/.
"""

import csv
import os
from datetime import datetime
from typing import List, Optional

from app.models import ListItem

# Diretório de exportação (relativo à raiz do projeto)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")


def ensure_exports_dir():
    """Garante que o diretório de exportação existe."""
    os.makedirs(EXPORTS_DIR, exist_ok=True)


def export_list_to_csv(list_id: int, list_name: str, items: List[ListItem]) -> str:
    """
    Gera arquivo CSV com os resultados da lista.
    Retorna o caminho absoluto do arquivo gerado.
    """
    ensure_exports_dir()

    # Nome de arquivo seguro com timestamp
    safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in list_name)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"list_{list_id}_{safe_name}_{timestamp}.csv"
    filepath = os.path.join(EXPORTS_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Cabeçalho
        writer.writerow([
            "email", "status", "reason", "normalized_reason",
            "technical_status", "confidence_score", "smtp_code",
            "provider", "technical_failure", "policy_block", "checked_at"
        ])
        # Linhas de dados
        for item in items:
            writer.writerow([
                item.email,
                item.status or "",
                item.reason or "",
                getattr(item, "normalized_reason", "") or "",
                getattr(item, "technical_status", "") or "",
                getattr(item, "confidence_score", 0) or 0,
                getattr(item, "smtp_code", "") or "",
                getattr(item, "provider", "") or "",
                getattr(item, "technical_failure", False),
                getattr(item, "policy_block", False),
                item.checked_at.isoformat() if item.checked_at else "",
            ])

    return filepath


def get_export_path(list_id: int) -> Optional[str]:
    """
    Procura o arquivo CSV mais recente para uma lista no diretório exports/.
    Retorna o caminho ou None se não encontrado.
    """
    ensure_exports_dir()
    prefix = f"list_{list_id}_"
    files = [
        f for f in os.listdir(EXPORTS_DIR)
        if f.startswith(prefix) and f.endswith(".csv")
    ]
    if not files:
        return None
    # Retorna o mais recente (por nome, pois tem timestamp)
    files.sort(reverse=True)
    return os.path.join(EXPORTS_DIR, files[0])
