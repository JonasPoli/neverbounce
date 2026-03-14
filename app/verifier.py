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

# Configurações padrão (podem ser sobrescritas via settings_service)
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
    mx_hosts, dns_error = _resolve_mx(domain)
    if dns_error == "timeout":
        return {
            "status": EmailStatus.UNKNOWN,
            "reason": "DNS timeout",
            "technical_status": "DNS_TIMEOUT",
            "confidence_score": 0
        }
    if mx_hosts is None:
        return {
            "status": EmailStatus.INVALID,
            "reason": "Domain does not resolve",
            "technical_status": "NXDOMAIN",
            "confidence_score": 100
        }
    if len(mx_hosts) == 0:
        return {
            "status": EmailStatus.INVALID,
            "reason": "Domain has no MX records",
            "technical_status": "NO_MX",
            "confidence_score": 90
        }

    # ── Nível 3 + 4: SMTP + heurística ACCEPT_ALL ───────────────────────
    return _check_smtp(email, domain, mx_hosts)


# ──────────────────────────────────────────────────────────────────────────────
# DNS
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_mx(domain: str) -> tuple:
    """
    Resolve registros MX do domínio.
    Fallback para registro A se não houver MX.
    Retorna (lista_de_hosts, erro_string).
    """
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        # Ordena por prioridade (menor = mais preferencial)
        mx_records = sorted(answers, key=lambda r: r.preference)
        return [str(r.exchange).rstrip(".") for r in mx_records], None
    except dns.resolver.NXDOMAIN:
        return None, "nxdomain"
    except dns.resolver.NoAnswer:
        # Sem MX — tenta fallback para registro A
        hosts = _fallback_a_record(domain)
        return (hosts, None) if hosts else ([], "no_mx")
    except dns.exception.Timeout:
        logger.warning(f"DNS timeout para domínio: {domain}")
        return [], "timeout"
    except Exception as e:
        logger.error(f"Erro DNS inesperado para {domain}: {e}")
        return None, str(e)


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
    Agora prioriza apenas a porta 25.
    """
    from app.database import SessionLocal
    from app.services import domain_service, settings_service
    
    db = SessionLocal()
    try:
        smtp_from = settings_service.get_setting(db, "smtp_from_email", "verify@emailcheck.brazil")
        smtp_helo = settings_service.get_setting(db, "smtp_helo_hostname", "mail.emailcheck.local")
        
        # Check domain-level accept-all from cache first
        is_catchall = domain_service.check_accept_all_cache(db, domain)
        if is_catchall:
            return {
                "status": EmailStatus.ACCEPT_ALL,
                "reason": "Server is catch-all (cached)",
                "technical_status": "CATCH_ALL_CACHED",
                "confidence_score": 100,
                "provider": _fingerprint_provider(mx_hosts[0])
            }

        last_result = None
        for mx_host in mx_hosts[:2]:  # Tenta até 2 MX hosts
            result = _smtp_probe(email, mx_host, 25, smtp_from, smtp_helo)
            if result:
                # Fingerprint provider
                result["provider"] = _fingerprint_provider(mx_host)
                
                if result["status"] == EmailStatus.VALID:
                    # Se o e-mail foi aceito, verifica ACCEPT_ALL no domínio
                    accept_all = _detect_accept_all(domain, mx_host, 25, smtp_from, smtp_helo)
                    if accept_all:
                        domain_service.set_accept_all(db, domain, True)
                        return {
                            "status": EmailStatus.ACCEPT_ALL,
                            "reason": "Server accepts all recipients",
                            "technical_status": "CATCH_ALL_CONFIRMED",
                            "confidence_score": 100,
                            "provider": result["provider"]
                        }
                    else:
                        domain_service.set_accept_all(db, domain, False)
                        result["confidence_score"] = 90 # High confidence for 250
                    return result
                
                if result["status"] == EmailStatus.INVALID:
                    result["confidence_score"] = 95 # High confidence for negative
                    return result
                
                last_result = result

        if last_result:
            return last_result

        return {
            "status": EmailStatus.UNKNOWN,
            "reason": "All SMTP attempts failed or timed out",
            "technical_status": "SMTP_ALL_FAILED",
            "confidence_score": 0
        }
    finally:
        db.close()


def _smtp_probe(email: str, mx_host: str, port: int, from_email: str, helo_hostname: str) -> Optional[Dict[str, str]]:
    """
    Sonda um único servidor SMTP.
    Retorna dict com status, reason, technical_status, smtp_code.
    """
    def attempt():
        try:
            smtp = smtplib.SMTP(mx_host, port, timeout=SMTP_TIMEOUT)
            with smtp:
                smtp.ehlo(helo_hostname)
                smtp.mail(from_email)
                code, message = smtp.rcpt(email)
                msg_str = message.decode(errors="ignore") if isinstance(message, bytes) else str(message)

                logger.debug(f"SMTP {email} via {mx_host}:{port} -> {code} {msg_str}")

                if code == 250:
                    return {
                        "status": EmailStatus.VALID,
                        "reason": f"SMTP 250: {msg_str[:80]}",
                        "technical_status": "MAILBOX_ACCEPTED",
                        "smtp_code": code
                    }
                elif code in (550, 551, 552, 553, 554):
                    return {
                        "status": EmailStatus.INVALID,
                        "reason": f"SMTP {code}: {msg_str[:80]}",
                        "technical_status": "MAILBOX_NOT_FOUND",
                        "smtp_code": code
                    }
                elif code in (450, 451, 452, 421):
                    return "greylist"
                else:
                    return {
                        "status": EmailStatus.UNKNOWN,
                        "reason": f"SMTP {code}: {msg_str[:80]}",
                        "technical_status": f"SMTP_CODE_{code}",
                        "smtp_code": code
                    }

        except smtplib.SMTPConnectError as e:
            return {"status": EmailStatus.UNKNOWN, "reason": f"Connect error: {str(e)[:50]}", "technical_status": "CONNECTION_ERROR"}
        except socket.timeout:
            return {"status": EmailStatus.UNKNOWN, "reason": "Connection timeout", "technical_status": "SMTP_TIMEOUT"}
        except (smtplib.SMTPException, OSError) as e:
            err_str = str(e).lower()
            tech = "SMTP_ERROR"
            if "blocked" in err_str or "reputation" in err_str:
                tech = "BLOCKED_IP"
            return {"status": EmailStatus.UNKNOWN, "reason": str(e)[:80], "technical_status": tech}

    # Primeira tentativa
    result = attempt()

    # Se for Greylisting, espera 5 segundos e tenta de novo
    if result == "greylist":
        import time
        time.sleep(5)
        result = attempt()
        if result == "greylist":
            return {
                "status": EmailStatus.UNKNOWN,
                "reason": "Greylisting persistent",
                "technical_status": "GREYLISTED",
                "smtp_code": 451
            }
    
    return result


def _fingerprint_provider(mx_host: str) -> str:
    """Identifica o provedor baseado no MX hostname."""
    mx_host = mx_host.lower()
    if "google" in mx_host or "gmail" in mx_host:
        return "GOOGLE"
    if "outlook" in mx_host or "protection.outlook" in mx_host or "hotmail" in mx_host:
        return "MICROSOFT"
    if "yahoo" in mx_host:
        return "YAHOO"
    if "uol.com.br" in mx_host:
        return "UOL"
    if "secureserver" in mx_host:
        return "GODADDY"
    return "OTHER"


# ──────────────────────────────────────────────────────────────────────────────
# Heurística ACCEPT_ALL
# ──────────────────────────────────────────────────────────────────────────────

def _detect_accept_all(domain: str, mx_host: str, port: int, from_email: str, helo: str) -> bool:
    """
    Detecta se o servidor aceita qualquer destinatário.
    Usa dois e-mails aleatórios para maior segurança.
    """
    try:
        # Primeiro probe
        fake1 = random_email_for_domain(domain)
        res1 = _smtp_probe(fake1, mx_host, port, from_email, helo)
        
        if res1 and res1["status"] == EmailStatus.VALID:
            # Segundo probe para confirmar
            fake2 = random_email_for_domain(domain)
            res2 = _smtp_probe(fake2, mx_host, port, from_email, helo)
            
            if res2 and res2["status"] == EmailStatus.VALID:
                logger.info(f"ACCEPT_ALL confirmado em {domain}")
                return True
        return False
    except Exception as e:
        logger.warning(f"Erro na heurística ACCEPT_ALL para {domain}: {e}")
        return False
