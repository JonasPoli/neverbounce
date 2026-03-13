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
    Realiza verificação SMTP nos MX hosts disponíveis.
    Tenta múltiplas portas (25, 587, 465) e lida com greylisting.
    """
    ports = [25, 587, 465]
    last_error_reason = "All SMTP attempts failed"

    for mx_host in mx_hosts[:3]:  # Tenta até 3 MX hosts
        for port in ports:
            result = _smtp_probe(email, mx_host, port)
            if result is not None:
                # Se o e-mail foi aceito (VALID), verifica ACCEPT_ALL
                if result["status"] == EmailStatus.VALID:
                    accept_all = _detect_accept_all(domain, mx_host, port)
                    if accept_all:
                        return {"status": EmailStatus.ACCEPT_ALL, "reason": "Server accepts all recipients"}
                
                # Se o resultado for definitivo (VALID ou INVALID), retornamos
                if result["status"] in (EmailStatus.VALID, EmailStatus.INVALID):
                    return result
                
                # Caso contrário (UNKNOWN), guardamos o detalhe e tentamos próxima porta/MX
                last_error_reason = result["reason"]

    return {"status": EmailStatus.UNKNOWN, "reason": f"SMTP Check failed: {last_error_reason}"}


def _smtp_probe(email: str, mx_host: str, port: int = 25) -> Optional[Dict[str, str]]:
    """
    Sonda um único servidor SMTP em uma porta específica.
    Retorna dict de resultado. Inclui suporte a STARTTLS e SSL/TLS.
    Lida com Greylisting (4xx) com retry único após delay.
    """
    def attempt():
        try:
            # Seleciona modo de conexão baseado na porta
            if port == 465:
                smtp = smtplib.SMTP_SSL(mx_host, port, timeout=SMTP_TIMEOUT)
            else:
                smtp = smtplib.SMTP(mx_host, port, timeout=SMTP_TIMEOUT)
                if port == 587:
                    smtp.starttls()
            
            with smtp:
                smtp.ehlo_or_helo_if_needed()
                smtp.mail(SMTP_FROM_EMAIL)
                code, message = smtp.rcpt(email)
                msg_str = message.decode(errors="ignore") if isinstance(message, bytes) else str(message)

                logger.debug(f"SMTP {email} via {mx_host}:{port} -> {code} {msg_str}")

                if code == 250:
                    return {"status": EmailStatus.VALID, "reason": f"SMTP 250: {msg_str[:80]}"}
                elif code in (550, 551, 552, 553, 554):
                    return {"status": EmailStatus.INVALID, "reason": f"SMTP {code}: {msg_str[:80]}"}
                elif code in (450, 451, 452, 421):
                    return "greylist"
                else:
                    return {"status": EmailStatus.UNKNOWN, "reason": f"SMTP {code}: {msg_str[:80]}"}

        except smtplib.SMTPConnectError:
            return None
        except (smtplib.SMTPException, socket.timeout, OSError) as e:
            return {"status": EmailStatus.UNKNOWN, "reason": str(e)[:80]}

    # Primeira tentativa
    result = attempt()

    # Se for Greylisting, espera 5 segundos e tenta de novo
    if result == "greylist":
        import time
        time.sleep(5)
        result = attempt()
        if result == "greylist":
            return {"status": EmailStatus.UNKNOWN, "reason": "Temporary failure (Greylisting) persistent after retry"}
    
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Heurística ACCEPT_ALL
# ──────────────────────────────────────────────────────────────────────────────

def _detect_accept_all(domain: str, mx_host: str, port: int = 25) -> bool:
    """
    Detecta se o servidor aceita qualquer destinatário (catchall/accept-all).
    """
    try:
        fake_email = random_email_for_domain(domain)
        result = _smtp_probe(fake_email, mx_host, port)
        if result and result["status"] == EmailStatus.VALID:
            logger.info(f"ACCEPT_ALL detectado em {domain} via porta {port}")
            return True
        return False
    except Exception as e:
        logger.warning(f"Erro na heurística ACCEPT_ALL para {domain}: {e}")
        return False
