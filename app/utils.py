"""
utils.py
--------
Funções utilitárias do sistema:
- Normalização de e-mails
- Deduplicação
- Leitura de CSV e XLSX
- Geração de e-mail aleatório para heurística ACCEPT_ALL
"""

import re
import random
import string
import io
from typing import List

import pandas as pd


# Regex de validação de sintaxe de e-mail
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def normalize_email(email: str) -> str:
    """
    Normaliza um endereço de e-mail:
    - Remove espaços em branco à esquerda e à direita
    - Converte para minúsculas
    """
    return email.strip().lower()


def is_valid_syntax(email: str) -> bool:
    """Verifica se o e-mail tem sintaxe válida via regex."""
    return bool(EMAIL_REGEX.match(email))


def deduplicate(emails: List[str]) -> List[str]:
    """
    Remove e-mails duplicados preservando a ordem de aparecimento.
    """
    seen = set()
    result = []
    for email in emails:
        if email not in seen:
            seen.add(email)
            result.append(email)
    return result


def random_email_for_domain(domain: str) -> str:
    """
    Gera um endereço de e-mail aleatório e improvável para o domínio dado.
    Usado na heurística de detecção de servidores ACCEPT_ALL.
    """
    random_local = "".join(random.choices(string.ascii_lowercase + string.digits, k=20))
    return f"{random_local}@{domain}"


def extract_domain(email: str) -> str:
    """Extrai o domínio de um endereço de e-mail."""
    return email.split("@")[1] if "@" in email else ""


def parse_emails_from_text(text: str) -> List[str]:
    """
    Extrai e-mails de um bloco de texto.
    Suporta uma por linha, separadas por vírgula, ponto e vírgula ou espaço.
    """
    # Substitui delimitadores comuns por nova linha
    text = re.sub(r"[,;\s]+", "\n", text)
    lines = [line.strip() for line in text.splitlines()]
    emails = [normalize_email(line) for line in lines if line]
    return [e for e in emails if e]  # Remove vazios


def parse_emails_from_csv(content: bytes) -> List[str]:
    """
    Lê e-mails de um arquivo CSV.
    Procura automaticamente a coluna que contém e-mails.
    Suporta arquivos com ou sem header.
    """
    try:
        df = pd.read_csv(io.BytesIO(content), dtype=str, header=0)
        emails = _extract_email_column(df)
        if emails:
            return emails
        # Tenta sem header
        df = pd.read_csv(io.BytesIO(content), dtype=str, header=None)
        return _extract_email_column(df)
    except Exception as e:
        raise ValueError(f"Erro ao processar CSV: {e}")


def parse_emails_from_xlsx(content: bytes) -> List[str]:
    """
    Lê e-mails de um arquivo XLSX.
    Procura automaticamente a coluna que contém e-mails.
    """
    try:
        df = pd.read_excel(io.BytesIO(content), dtype=str)
        emails = _extract_email_column(df)
        if emails:
            return emails
        df = pd.read_excel(io.BytesIO(content), dtype=str, header=None)
        return _extract_email_column(df)
    except Exception as e:
        raise ValueError(f"Erro ao processar XLSX: {e}")


def _extract_email_column(df: pd.DataFrame) -> List[str]:
    """
    Tenta identificar qual coluna contém endereços de e-mail.
    Estratégia: procura coluna com nome 'email' primeiro,
    depois itera pelas colunas buscando a que tem mais e-mails válidos.
    """
    emails = []

    # Passo 1: procura coluna com nome "email" (case-insensitive)
    for col in df.columns:
        if str(col).strip().lower() in ("email", "e-mail", "emails", "e-mails", "mail"):
            col_values = df[col].dropna().astype(str).tolist()
            emails = [normalize_email(v) for v in col_values if is_valid_syntax(normalize_email(v))]
            if emails:
                return emails

    # Passo 2: itera colunas e pega a com mais e-mails válidos
    best_col = None
    best_count = 0
    for col in df.columns:
        col_values = df[col].dropna().astype(str).tolist()
        valid = [normalize_email(v) for v in col_values if is_valid_syntax(normalize_email(v))]
        if len(valid) > best_count:
            best_count = len(valid)
            best_col = valid

    return best_col if best_col else []
