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
        return {
            "status": EmailStatus.INVALID,
            "reason": "Invalid syntax",
            "technical_status": "INVALID_SYNTAX",
            "confidence_score": 100
        }

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
                    # Só marca como ACCEPT_ALL se o teste de catch-all não for inconclusivo
                    if accept_all == True:
                        domain_service.set_accept_all(db, domain, True)
                        return {
                            "status": EmailStatus.ACCEPT_ALL,
                            "reason": "Server accepts all recipients",
                            "technical_status": "CATCH_ALL_CONFIRMED",
                            "confidence_score": 100,
                            "provider": result["provider"]
                        }
                    elif accept_all == False:
                        domain_service.set_accept_all(db, domain, False)
                        result["confidence_score"] = 90
                    else: # accept_all == "unknown"
                        # Se a detecção de catch-all falhou por motivo técnico, 
                        # prefere UNKNOWN para este e-mail específico também
                        return {
                            "status": EmailStatus.UNKNOWN,
                            "reason": "Catch-all detection ambiguous (blocked or failure)",
                            "technical_status": "CATCH_ALL_AMBIGUOUS",
                            "confidence_score": 30
                        }
                    return result
                
                # Se for INVALID, retornamos imediatamente (só vira INVALID se for erro forte)
                if result["status"] == EmailStatus.INVALID:
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
    """
    def attempt():
        try:
            smtp = smtplib.SMTP(mx_host, port, timeout=SMTP_TIMEOUT)
            with smtp:
                smtp.ehlo(helo_hostname)
                smtp.mail(from_email)
                code, message = smtp.rcpt(email)
                msg_str = (message.decode(errors="ignore") if isinstance(message, bytes) else str(message)).strip()

                logger.debug(f"SMTP {email} via {mx_host}:{port} -> {code} {msg_str}")
                
                return _normalize_smtp_response(code, msg_str)

        except smtplib.SMTPConnectError as e:
            return {"status": EmailStatus.UNKNOWN, "reason": f"Connect error: {str(e)[:50]}", "technical_status": "CONNECTION_ERROR", "confidence_score": 0}
        except socket.timeout:
            return {"status": EmailStatus.UNKNOWN, "reason": "Connection timeout", "technical_status": "SMTP_TIMEOUT", "confidence_score": 0}
        except (smtplib.SMTPException, OSError) as e:
            err_str = str(e).lower()
            tech = "SMTP_ERROR"
            if any(k in err_str for k in ["blocked", "reputation", "blacklist", "spamhaus", "denied"]):
                tech = "BLOCKED_IP"
            return {"status": EmailStatus.UNKNOWN, "reason": str(e)[:80], "technical_status": tech, "confidence_score": 0}

    # Primeira tentativa
    result = attempt()

    # Se for "retry" (greylist ou falha temporária), espera e tenta de novo
    if result == "retry":
        import time
        time.sleep(5)
        result = attempt()
        if result == "retry":
            return {
                "status": EmailStatus.UNKNOWN,
                "reason": "Persistent temporary failure (Greylisting/RateLimit)",
                "technical_status": "DEFERRED",
                "confidence_score": 20
            }
    
    return result


def _normalize_smtp_response(code: int, message: str) -> Dict[str, str]:
    """
    Normaliza a resposta SMTP para VALID, INVALID ou UNKNOWN.
    Evita falsos INVALIDs transformando ambiguidades em UNKNOWN.
    """
    msg = message.lower()
    
    # ── Sucesso Real ────────────────────────────────────────────────
    if code == 250:
        return {
            "status": EmailStatus.VALID,
            "reason": f"Success: {message[:80]}",
            "technical_status": "MAILBOX_ACCEPTED",
            "smtp_code": code,
            "confidence_score": 90
        }

    # ── Erros Temporários / Greylisting ──────────────────────────────
    if code in (450, 451, 452, 421) or any(k in msg for k in ["try again", "rate limit", "too many", "temporary"]):
        return "retry"

    # ── Erros de Rejeição por Política/Reputação (Vira UNKNOWN) ──────
    # Muitas vezes erros 550 ou 554 são bloqueios de IP ou Spam Filter
    if any(k in msg for k in ["blocked", "reputation", "blacklist", "spamhaus", "dnsbl", "filter", "denied", "policy", "helo", "ptr"]):
        return {
            "status": EmailStatus.UNKNOWN,
            "reason": f"Blocked/Policy: {message[:80]}",
            "technical_status": "BLOCKED_OR_POLICY",
            "smtp_code": code,
            "confidence_score": 10
        }

    # ── Erros Definitivos (Vira INVALID) ──────────────────────────────
    # Apenas se contiver palavras-chave fortes de inexistência
    invalid_keywords = [
        "no such user", "does not exist", "mailbox unavailable", 
        "recipient rejected", "user unknown", "not found", 
        "invalid recipient", "account is disabled"
    ]
    if code >= 500 and any(k in msg for k in invalid_keywords):
        return {
            "status": EmailStatus.INVALID,
            "reason": f"Invalid: {message[:80]}",
            "technical_status": "MAILBOX_NOT_FOUND",
            "smtp_code": code,
            "confidence_score": 95
        }

    # ── Em caso de dúvida, retorne UNKNOWN ───────────────────────────
    # Se caiu num 5xx mas não identificamos o motivo exato, não arriscamos INVALID
    return {
        "status": EmailStatus.UNKNOWN,
        "reason": f"Ambiguous SMTP {code}: {message[:80]}",
        "technical_status": f"SMTP_UNCERTAIN_{code}",
        "smtp_code": code,
        "confidence_score": 30
    }


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

def _detect_accept_all(domain: str, mx_host: str, port: int, from_email: str, helo: str) -> any:
    """
    Detecta se o servidor aceita qualquer destinatário.
    Usa dois e-mails aleatórios para maior segurança.
    Retorna: True (catch-all), False (rejeitou fakes), "unknown" (bloqueio/erro).
    """
    try:
        # Primeiro probe
        fake1 = random_email_for_domain(domain)
        res1 = _smtp_probe(fake1, mx_host, port, from_email, helo)
        
        # Se for bloqueio ou falha técnica no probe do fake, não podemos decidir
        if not res1 or res1["status"] == EmailStatus.UNKNOWN:
            return "unknown"

        if res1["status"] == EmailStatus.VALID:
            # Segundo probe para confirmar
            fake2 = random_email_for_domain(domain)
            res2 = _smtp_probe(fake2, mx_host, port, from_email, helo)
            
            if not res2 or res2["status"] == EmailStatus.UNKNOWN:
                return "unknown"

            if res2["status"] == EmailStatus.VALID:
                logger.info(f"ACCEPT_ALL confirmado em {domain}")
                return True
        
        return False
    except Exception as e:
        logger.warning(f"Erro na heurística ACCEPT_ALL para {domain}: {e}")
        return "unknown"
