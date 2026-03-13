"""
verifier.py
-----------
Motor central de verificação de e-mails em 4 níveis:
  1. Sintaxe (regex)
  2. DNS / MX
  3. SMTP (RCPT TO)
  4. Heurística ACCEPT_ALL

Retorna sempre um dict com { status, reason }.
"""

import re
import smtplib
import socket
import logging
from typing import Dict, Optional

import dns.resolver
import dns.exception

from app.utils import (
    is_valid_syntax,
    extract_domain,
    random_email_for_domain,
)
from app.models import EmailStatus

logger = logging.getLogger(__name__)

# E-mail remetente técnico usado nas sondagens SMTP
SMTP_FROM_EMAIL = "verify@emailcheck.local"
SMTP_TIMEOUT = 10  # segundos
SMTP_PORT = 25


def verify_email(email: str) -> Dict[str, str]:
    """
    Executa os 4 níveis de verificação de um e-mail.
    Retorna dict: { "status": str, "reason": str }

    Nota: a consulta ao cache global é feita em tasks.py antes de chamar
    esta função, mantendo a separação de responsabilidades.
    """
    # ── Nível 1: Sintaxe ────────────────────────────────────────────────
    if not is_valid_syntax(email):
        return {"status": EmailStatus.INVALID, "reason": "Invalid syntax"}

    domain = extract_domain(email)

    # ── Nível 2: DNS / MX ───────────────────────────────────────────────
    mx_hosts = _resolve_mx(domain)
    if mx_hosts is None:
        return {"status": EmailStatus.INVALID, "reason": "Domain does not resolve"}
    if len(mx_hosts) == 0:
        return {"status": EmailStatus.INVALID, "reason": "Domain has no MX records"}

    # ── Nível 3 + 4: SMTP + heurística ACCEPT_ALL ───────────────────────
    return _check_smtp(email, domain, mx_hosts)


# ──────────────────────────────────────────────────────────────────────────────
# DNS
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_mx(domain: str) -> Optional[list]:
    """
    Resolve registros MX do domínio.
    Fallback para registro A se não houver MX.
    Retorna lista de hostnames ordenada por prioridade, ou None em caso de falha.
    """
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        # Ordena por prioridade (menor = mais preferencial)
        mx_records = sorted(answers, key=lambda r: r.preference)
        return [str(r.exchange).rstrip(".") for r in mx_records]
    except dns.resolver.NXDOMAIN:
        return None  # Domínio não existe
    except dns.resolver.NoAnswer:
        # Sem MX — tenta fallback para registro A
        return _fallback_a_record(domain)
    except dns.exception.Timeout:
        logger.warning(f"DNS timeout para domínio: {domain}")
        return []  # Timeout — retorna lista vazia (sem MX mas domínio pode existir)
    except Exception as e:
        logger.error(f"Erro DNS inesperado para {domain}: {e}")
        return None


def _fallback_a_record(domain: str):
    """Tenta resolver registro A como fallback de MX."""
    try:
        dns.resolver.resolve(domain, "A", lifetime=5)
        return [domain]  # Usa o próprio domínio como MX
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# SMTP
# ──────────────────────────────────────────────────────────────────────────────

def _check_smtp(email: str, domain: str, mx_hosts: list) -> Dict[str, str]:
    """
    Realiza verificação SMTP no primeiro MX disponível.
    Inclui heurística ACCEPT_ALL.
    """
    for mx_host in mx_hosts[:3]:  # Tenta até 3 MX hosts
        result = _smtp_probe(email, mx_host)
        if result is not None:
            # Se o e-mail foi aceito (VALID), verifica ACCEPT_ALL
            if result["status"] == EmailStatus.VALID:
                accept_all = _detect_accept_all(domain, mx_host)
                if accept_all:
                    return {"status": EmailStatus.ACCEPT_ALL, "reason": "Server accepts all recipients"}
            return result
        # Se result é None, tenta o próximo MX

    return {"status": EmailStatus.UNKNOWN, "reason": "Could not connect to any MX server"}


def _smtp_probe(email: str, mx_host: str) -> Optional[Dict[str, str]]:
    """
    Sonda um único servidor SMTP.
    Retorna dict de resultado ou None se não conseguiu conectar.
    """
    try:
        with smtplib.SMTP(timeout=SMTP_TIMEOUT) as smtp:
            smtp.connect(mx_host, SMTP_PORT)
            smtp.ehlo_or_helo_if_needed()
            smtp.mail(SMTP_FROM_EMAIL)

            code, message = smtp.rcpt(email)
            msg_str = message.decode(errors="ignore") if isinstance(message, bytes) else str(message)

            logger.debug(f"SMTP {email} via {mx_host}: {code} {msg_str}")

            if code == 250:
                return {"status": EmailStatus.VALID, "reason": f"SMTP 250: {msg_str[:80]}"}
            elif code in (550, 551, 552, 553, 554):
                return {"status": EmailStatus.INVALID, "reason": f"SMTP {code}: {msg_str[:80]}"}
            elif code == 421:
                # Servidor temporariamente indisponível
                return {"status": EmailStatus.UNKNOWN, "reason": f"SMTP {code}: server temporarily unavailable"}
            elif code in (450, 451, 452):
                # Erros temporários / greylisting
                return {"status": EmailStatus.UNKNOWN, "reason": f"SMTP {code}: temporary error or greylisting"}
            else:
                return {"status": EmailStatus.UNKNOWN, "reason": f"SMTP {code}: {msg_str[:80]}"}

    except smtplib.SMTPConnectError as e:
        logger.warning(f"Falha ao conectar em {mx_host}: {e}")
        return None  # Tenta próximo MX
    except smtplib.SMTPServerDisconnected:
        return {"status": EmailStatus.UNKNOWN, "reason": "Server disconnected unexpectedly"}
    except smtplib.SMTPException as e:
        return {"status": EmailStatus.UNKNOWN, "reason": f"SMTP error: {str(e)[:80]}"}
    except socket.timeout:
        return {"status": EmailStatus.UNKNOWN, "reason": "SMTP connection timed out"}
    except OSError as e:
        logger.warning(f"Erro de socket em {mx_host}: {e}")
        return None  # Tenta próximo MX


# ──────────────────────────────────────────────────────────────────────────────
# Heurística ACCEPT_ALL
# ──────────────────────────────────────────────────────────────────────────────

def _detect_accept_all(domain: str, mx_host: str) -> bool:
    """
    Detecta se o servidor aceita qualquer destinatário (catchall/accept-all).

    Estratégia:
    1. Gera um e-mail aleatório e improvável para o mesmo domínio
    2. Sonda via SMTP
    3. Se também retornar 250, o servidor provavelmente aceita tudo → ACCEPT_ALL

    Isolado e comentado conforme especificação.
    """
    try:
        fake_email = random_email_for_domain(domain)
        result = _smtp_probe(fake_email, mx_host)
        if result and result["status"] == EmailStatus.VALID:
            logger.info(f"ACCEPT_ALL detectado em {domain}: e-mail falso '{fake_email}' foi aceito")
            return True
        return False
    except Exception as e:
        # Nunca deixa a heurística quebrar o fluxo principal
        logger.warning(f"Erro na heurística ACCEPT_ALL para {domain}: {e}")
        return False
