"""
verifier.py
-----------
Motor central de verificação de e-mails em 4 níveis:
  1. Sintaxe (regex)
  2. DNS / MX
  3. SMTP (RCPT TO)  →  normalize_smtp_outcome()
  4. Accept-All       →  detect_accept_all_behavior()
  5. Decisão final    →  decide_final_status()

Retorna sempre um dict completo com campos de rastreabilidade.
"""

import re
import random
import smtplib
import socket
import logging
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Dict, Optional, List

import dns.resolver
import dns.exception

from app.utils import (
    is_valid_syntax,
    extract_domain,
    random_email_for_domain,
)
from app.models import EmailStatus

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 10  # segundos

# ══════════════════════════════════════════════════════════════════════════════
# TABELAS CENTRALIZADAS DE CLASSIFICAÇÃO
# ══════════════════════════════════════════════════════════════════════════════

DEFINITE_INVALID_PATTERNS: List[str] = [
    "user unknown",
    "no such user",
    "unknown user",
    "mailbox not found",
    "mailbox does not exist",
    "does not exist",
    "recipient address rejected: user unknown",
    "invalid recipient",
    "recipient rejected",
    "unknown local part",
    "mailbox unavailable",
    "account is disabled",
    "account has been disabled",
    "user not found",
    "recipient not found",
    "no mailbox here",
    "is not a valid mailbox",
    "address rejected",
    "undeliverable address",
    "unknown recipient",
    "no such recipient",
    "recipient unknown",
]

TECHNICAL_FAILURE_PATTERNS: List[str] = [
    "timed out",
    "timeout",
    "connection unexpectedly closed",
    "connection reset",
    "connect error",
    "network is unreachable",
    "temporary lookup failure",
    "dns failure",
    "read error",
    "broken pipe",
    "connection refused",
    "eof",
    "connection closed",
]

POLICY_BLOCK_PATTERNS: List[str] = [
    "client host blocked",
    "blocked using spamhaus",
    "spamhaus",
    "blacklist",
    "blacklisted",
    "dnsbl",
    "rejected for policy reasons",
    "policy rejection",
    "access denied",
    "sender address rejected",
    "bad helo",
    "ptr record",
    "reverse dns",
    "rdns",
    "helo command rejected",
    "ip blocked",
    "reputation",
    "rejected by filter",
    "mail from rejected",
    "not allowed",
    "poor reputation",
    "your ip",
    "rbl",
    "denied",
    "too many invalid",
]

GREYLIST_PATTERNS: List[str] = [
    "greylist",
    "graylist",
    "try again later",
    "temporarily deferred",
    "temporarily rejected",
    "please retry",
    "rate limit",
    "too many connections",
    "too many recipients",
    "temporary failure",
    "service temporarily unavailable",
]


# ══════════════════════════════════════════════════════════════════════════════
# ESTRUTURA DE RESULTADO NORMALIZADO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SmtpOutcome:
    """Estrutura normalizada de uma resposta SMTP."""
    smtp_code: Optional[int] = None
    raw_message: str = ""
    normalized_reason: str = ""
    outcome_type: str = "ambiguous"  # accepted | invalid_recipient | technical_failure | temporary_failure | policy_block | sender_blocked | ambiguous | catch_all_hint
    retryable: bool = False
    technical_failure: bool = False
    policy_block: bool = False
    recipient_rejected: bool = False
    accept_hint: bool = False


@dataclass
class AcceptAllResult:
    """Estrutura intermediária de detecção de catch-all."""
    accept_all_score: float = 0.0
    accept_all_reason: str = ""
    random_outcome_type: str = ""
    real_outcome_type: str = ""
    technical_contamination: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# CAMADA A: normalize_smtp_outcome
# ══════════════════════════════════════════════════════════════════════════════

def normalize_smtp_outcome(
    code: Optional[int] = None,
    message: str = "",
    exception: Optional[Exception] = None,
) -> SmtpOutcome:
    """
    Transforma código SMTP, mensagem textual e exceção em uma estrutura
    normalizada e classificada.
    """
    msg = " ".join(message.lower().split())  # case-insensitive + normaliza espaços

    # ── Exceções de rede/socket/SMTP ────────────────────────────────
    if exception is not None:
        exc_str = " ".join(str(exception).lower().split())
        
        # Patterns de bloqueio/policy dentro de exceções
        if _matches_any(exc_str, POLICY_BLOCK_PATTERNS):
            return SmtpOutcome(
                raw_message=str(exception)[:120],
                normalized_reason="smtp_policy_block",
                outcome_type="policy_block",
                policy_block=True,
            )
        
        # Greylist/temporário dentro de exceções
        if _matches_any(exc_str, GREYLIST_PATTERNS):
            return SmtpOutcome(
                raw_message=str(exception)[:120],
                normalized_reason="smtp_greylisted",
                outcome_type="temporary_failure",
                retryable=True,
                technical_failure=True,
            )
        
        # Default: falha técnica genérica
        return SmtpOutcome(
            raw_message=str(exception)[:120],
            normalized_reason="smtp_connection_error",
            outcome_type="technical_failure",
            retryable=True,
            technical_failure=True,
        )

    # ── Código 250: aceito ──────────────────────────────────────────
    if code == 250:
        return SmtpOutcome(
            smtp_code=code,
            raw_message=message[:120],
            normalized_reason="accepted_recipient",
            outcome_type="accepted",
            accept_hint=True,
        )

    # ── 4xx: temporário/greylist ────────────────────────────────────
    if code is not None and 400 <= code < 500:
        if _matches_any(msg, GREYLIST_PATTERNS):
            reason = "smtp_greylisted"
        else:
            reason = "smtp_temporary_failure"
        return SmtpOutcome(
            smtp_code=code,
            raw_message=message[:120],
            normalized_reason=reason,
            outcome_type="temporary_failure",
            retryable=True,
            technical_failure=True,
        )

    # ── 5xx: requer análise detalhada ───────────────────────────────
    if code is not None and code >= 500:
        # 1) Checar bloqueio/policy PRIMEIRO (mais prioritário que invalid)
        if _matches_any(msg, POLICY_BLOCK_PATTERNS):
            return SmtpOutcome(
                smtp_code=code,
                raw_message=message[:120],
                normalized_reason="smtp_policy_block",
                outcome_type="policy_block",
                policy_block=True,
            )
        
        # 2) Checar greylist/temporário em mensagem 5xx
        if _matches_any(msg, GREYLIST_PATTERNS):
            return SmtpOutcome(
                smtp_code=code,
                raw_message=message[:120],
                normalized_reason="smtp_greylisted",
                outcome_type="temporary_failure",
                retryable=True,
                technical_failure=True,
            )
        
        # 3) Checar falha técnica em mensagem 5xx
        if _matches_any(msg, TECHNICAL_FAILURE_PATTERNS):
            return SmtpOutcome(
                smtp_code=code,
                raw_message=message[:120],
                normalized_reason="smtp_technical_error",
                outcome_type="technical_failure",
                retryable=True,
                technical_failure=True,
            )

        # 4) Checar invalid_recipient definitivo
        if _matches_any(msg, DEFINITE_INVALID_PATTERNS):
            return SmtpOutcome(
                smtp_code=code,
                raw_message=message[:120],
                normalized_reason="invalid_recipient",
                outcome_type="invalid_recipient",
                recipient_rejected=True,
            )
        
        # 5) 5xx sem match claro → ambíguo (NÃO invalida)
        return SmtpOutcome(
            smtp_code=code,
            raw_message=message[:120],
            normalized_reason="smtp_ambiguous_rejection",
            outcome_type="ambiguous",
        )

    # ── Qualquer outro código ───────────────────────────────────────
    return SmtpOutcome(
        smtp_code=code,
        raw_message=message[:120],
        normalized_reason="smtp_unknown_response",
        outcome_type="ambiguous",
    )


def _matches_any(text: str, patterns: List[str]) -> bool:
    """Verifica se algum dos patterns aparece no texto (case-insensitive, já normalizado)."""
    return any(p in text for p in patterns)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS: should_retry / is_definitive_invalid
# ══════════════════════════════════════════════════════════════════════════════

def should_retry(outcome: SmtpOutcome) -> bool:
    """Decide se uma tentativa merece retry."""
    return outcome.retryable and outcome.outcome_type in (
        "temporary_failure", "technical_failure"
    )


def is_definitive_invalid(outcome: SmtpOutcome) -> bool:
    """INVALID exige prova forte: recipient_rejected SEM contaminação técnica/policy."""
    return (
        outcome.outcome_type == "invalid_recipient"
        and not outcome.technical_failure
        and not outcome.policy_block
    )


# ══════════════════════════════════════════════════════════════════════════════
# CAMADA B: detect_accept_all_behavior
# ══════════════════════════════════════════════════════════════════════════════

def detect_accept_all_behavior(
    real_outcome: SmtpOutcome,
    random_outcome: SmtpOutcome,
) -> AcceptAllResult:
    """
    Analisa comportamento do domínio comparando resposta do endereço real
    com resposta de um endereço randômico inexistente.
    """
    result = AcceptAllResult(
        real_outcome_type=real_outcome.outcome_type,
        random_outcome_type=random_outcome.outcome_type,
    )

    # Contaminação técnica: se qualquer probe teve falha de rede/policy
    if (real_outcome.technical_failure or real_outcome.policy_block
        or random_outcome.technical_failure or random_outcome.policy_block):
        result.technical_contamination = True

    # Score
    score = 0.0

    if real_outcome.outcome_type == "accepted":
        score += 0.3

    if random_outcome.outcome_type == "accepted":
        score += 0.5

    if random_outcome.outcome_type == "ambiguous":
        score += 0.1

    if random_outcome.technical_failure or random_outcome.policy_block:
        score -= 0.6

    if real_outcome.technical_failure or real_outcome.policy_block:
        score -= 0.4

    result.accept_all_score = round(max(0.0, min(1.0, score)), 2)

    # Razão textual
    if result.accept_all_score >= 0.7 and not result.technical_contamination:
        result.accept_all_reason = "catch_all_detected"
    elif (real_outcome.outcome_type == "accepted"
          and random_outcome.outcome_type == "invalid_recipient"):
        result.accept_all_reason = "not_catch_all"
    elif result.technical_contamination:
        result.accept_all_reason = "test_contaminated"
    else:
        result.accept_all_reason = "inconclusive"

    return result


# ══════════════════════════════════════════════════════════════════════════════
# CAMADA C: decide_final_status
# ══════════════════════════════════════════════════════════════════════════════

def decide_final_status(
    real_outcome: SmtpOutcome,
    accept_all: Optional[AcceptAllResult] = None,
    provider: str = "OTHER",
) -> Dict:
    """
    Matriz de decisão final centralizada.
    """
    # ── 1. Real = invalid_recipient definitivo ──────────────────────
    if is_definitive_invalid(real_outcome):
        return _build_result(
            status=EmailStatus.INVALID,
            normalized_reason="invalid_recipient",
            outcome=real_outcome,
            confidence=95,
            provider=provider,
        )

    # ── 2. Real com falha técnica ou bloqueio de política ───────────
    if real_outcome.technical_failure or real_outcome.policy_block:
        return _build_result(
            status=EmailStatus.UNKNOWN,
            normalized_reason=real_outcome.normalized_reason,
            outcome=real_outcome,
            confidence=10,
            provider=provider,
            technical_failure=True,
            retryable=real_outcome.retryable,
            policy_block=real_outcome.policy_block,
        )

    # ── 3. Real = ambíguo ──────────────────────────────────────────
    if real_outcome.outcome_type == "ambiguous":
        return _build_result(
            status=EmailStatus.UNKNOWN,
            normalized_reason=real_outcome.normalized_reason,
            outcome=real_outcome,
            confidence=30,
            provider=provider,
        )

    # ── 4. Real = accepted ─────────────────────────────────────────
    if real_outcome.outcome_type == "accepted":
        if accept_all is None:
            # Sem teste de catch-all (não deveria acontecer, mas safety)
            return _build_result(
                status=EmailStatus.VALID,
                normalized_reason="accepted_recipient",
                outcome=real_outcome,
                confidence=85,
                provider=provider,
            )

        # 4a. Accept-All com contaminação técnica forte
        if accept_all.technical_contamination:
            # Se random falhou por motivo técnico, não podemos
            # confirmar nem negar catch-all. Conservadoramente: VALID
            # desde que o real esteja limpo
            if not real_outcome.technical_failure and not real_outcome.policy_block:
                return _build_result(
                    status=EmailStatus.VALID,
                    normalized_reason="accepted_recipient",
                    outcome=real_outcome,
                    confidence=70,
                    provider=provider,
                    accept_all_score=accept_all.accept_all_score,
                )
            else:
                return _build_result(
                    status=EmailStatus.UNKNOWN,
                    normalized_reason="test_contaminated",
                    outcome=real_outcome,
                    confidence=20,
                    provider=provider,
                    technical_failure=True,
                    accept_all_score=accept_all.accept_all_score,
                )

        # 4b. Random = invalid_recipient → VALID (prova que NÃO é catch-all)
        if accept_all.random_outcome_type == "invalid_recipient":
            return _build_result(
                status=EmailStatus.VALID,
                normalized_reason="accepted_recipient",
                outcome=real_outcome,
                confidence=92,
                provider=provider,
                accept_all_score=accept_all.accept_all_score,
            )

        # 4c. Score alto de catch-all sem contaminação → ACCEPT_ALL
        if accept_all.accept_all_score >= 0.7:
            return _build_result(
                status=EmailStatus.ACCEPT_ALL,
                normalized_reason="catch_all_detected",
                outcome=real_outcome,
                confidence=90,
                provider=provider,
                accept_all_score=accept_all.accept_all_score,
            )

        # 4d. Random = ambíguo → depende do score
        if accept_all.accept_all_score >= 0.4:
            return _build_result(
                status=EmailStatus.ACCEPT_ALL,
                normalized_reason="catch_all_likely",
                outcome=real_outcome,
                confidence=60,
                provider=provider,
                accept_all_score=accept_all.accept_all_score,
            )

        # 4e. Sem evidência forte de catch-all → VALID
        return _build_result(
            status=EmailStatus.VALID,
            normalized_reason="accepted_recipient",
            outcome=real_outcome,
            confidence=80,
            provider=provider,
            accept_all_score=accept_all.accept_all_score,
        )

    # ── Fallback: qualquer caso não mapeado → UNKNOWN ──────────────
    return _build_result(
        status=EmailStatus.UNKNOWN,
        normalized_reason="unmapped_outcome",
        outcome=real_outcome,
        confidence=0,
        provider=provider,
    )


def _build_result(
    status: str,
    normalized_reason: str,
    outcome: SmtpOutcome,
    confidence: int,
    provider: str = "OTHER",
    technical_failure: bool = False,
    retryable: bool = False,
    policy_block: bool = False,
    accept_all_score: float = 0.0,
) -> Dict:
    return {
        "status": status,
        "reason": outcome.raw_message[:120] if outcome.raw_message else normalized_reason,
        "normalized_reason": normalized_reason,
        "technical_status": outcome.outcome_type.upper(),
        "smtp_code": outcome.smtp_code,
        "confidence_score": confidence,
        "provider": provider,
        "technical_failure": technical_failure or outcome.technical_failure,
        "retryable": retryable or outcome.retryable,
        "policy_block": policy_block or outcome.policy_block,
        "accept_all_score": accept_all_score,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DNS
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_mx(domain: str) -> tuple:
    """Resolve MX. Retorna (hosts, erro_string)."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        mx_records = sorted(answers, key=lambda r: r.preference)
        return [str(r.exchange).rstrip(".") for r in mx_records], None
    except dns.resolver.NXDOMAIN:
        return None, "nxdomain"
    except dns.resolver.NoAnswer:
        hosts = _fallback_a_record(domain)
        return (hosts, None) if hosts else ([], "no_mx")
    except dns.exception.Timeout:
        logger.warning(f"DNS timeout para domínio: {domain}")
        return [], "timeout"
    except Exception as e:
        logger.error(f"Erro DNS inesperado para {domain}: {e}")
        return None, str(e)


def _fallback_a_record(domain: str):
    try:
        dns.resolver.resolve(domain, "A", lifetime=5)
        return [domain]
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PROVIDER FINGERPRINT
# ══════════════════════════════════════════════════════════════════════════════

def _fingerprint_provider(mx_host: str) -> str:
    mx = mx_host.lower()
    if "google" in mx or "gmail" in mx:
        return "GOOGLE"
    if "outlook" in mx or "protection.outlook" in mx or "hotmail" in mx:
        return "MICROSOFT"
    if "yahoo" in mx:
        return "YAHOO"
    if "uol.com.br" in mx:
        return "UOL"
    if "secureserver" in mx:
        return "GODADDY"
    if "locaweb" in mx:
        return "LOCAWEB"
    if "zoho" in mx:
        return "ZOHO"
    return "OTHER"


# ══════════════════════════════════════════════════════════════════════════════
# SMTP PROBE (baixo nível)
# ══════════════════════════════════════════════════════════════════════════════

def _smtp_connect_and_rcpt(
    email: str, mx_host: str, port: int, from_email: str, helo: str
) -> SmtpOutcome:
    """Executa uma única conexão SMTP e retorna o outcome normalizado."""
    try:
        smtp = smtplib.SMTP(mx_host, port, timeout=SMTP_TIMEOUT)
        with smtp:
            smtp.ehlo(helo)
            smtp.mail(from_email)
            code, message = smtp.rcpt(email)
            msg_str = (
                message.decode(errors="ignore")
                if isinstance(message, bytes)
                else str(message)
            ).strip()
            logger.debug(f"SMTP {email} via {mx_host}:{port} -> {code} {msg_str}")
            return normalize_smtp_outcome(code=code, message=msg_str)
    except Exception as e:
        logger.debug(f"SMTP {email} via {mx_host}:{port} -> EXCEPTION {e}")
        return normalize_smtp_outcome(exception=e)


def _smtp_probe_with_retry(
    email: str, mx_host: str, port: int, from_email: str, helo: str,
    max_retries: int = 2,
) -> SmtpOutcome:
    """Probe com backoff exponencial + jitter para falhas temporárias."""
    outcome = _smtp_connect_and_rcpt(email, mx_host, port, from_email, helo)

    for attempt in range(1, max_retries + 1):
        if not should_retry(outcome):
            break
        # Backoff: 3s, 7s, 15s... com jitter
        delay = (2 ** attempt) + random.uniform(0.5, 2.0)
        logger.debug(f"Retry {attempt}/{max_retries} em {delay:.1f}s para {email}@{mx_host}")
        time.sleep(delay)
        outcome = _smtp_connect_and_rcpt(email, mx_host, port, from_email, helo)

    return outcome


# ══════════════════════════════════════════════════════════════════════════════
# ORQUESTRAÇÃO PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def verify_email(email: str) -> Dict:
    """
    Ponto de entrada principal. Executa os 4 níveis de verificação.
    """
    # ── Nível 1: Sintaxe ────────────────────────────────────────────
    if not is_valid_syntax(email):
        return {
            "status": EmailStatus.INVALID,
            "reason": "Invalid syntax",
            "normalized_reason": "invalid_syntax",
            "technical_status": "INVALID_SYNTAX",
            "confidence_score": 100,
            "technical_failure": False,
            "retryable": False,
            "policy_block": False,
            "accept_all_score": 0.0,
        }

    domain = extract_domain(email)

    # ── Nível 2: DNS / MX ───────────────────────────────────────────
    mx_hosts, dns_error = _resolve_mx(domain)

    if dns_error == "timeout":
        return {
            "status": EmailStatus.UNKNOWN,
            "reason": "DNS timeout",
            "normalized_reason": "smtp_timeout",
            "technical_status": "DNS_TIMEOUT",
            "confidence_score": 0,
            "technical_failure": True,
            "retryable": True,
            "policy_block": False,
            "accept_all_score": 0.0,
        }
    if mx_hosts is None:
        return {
            "status": EmailStatus.INVALID,
            "reason": "Domain does not resolve",
            "normalized_reason": "invalid_domain",
            "technical_status": "NXDOMAIN",
            "confidence_score": 100,
            "technical_failure": False,
            "retryable": False,
            "policy_block": False,
            "accept_all_score": 0.0,
        }
    if len(mx_hosts) == 0:
        return {
            "status": EmailStatus.INVALID,
            "reason": "Domain has no MX records",
            "normalized_reason": "invalid_domain",
            "technical_status": "NO_MX",
            "confidence_score": 90,
            "technical_failure": False,
            "retryable": False,
            "policy_block": False,
            "accept_all_score": 0.0,
        }

    # ── Nível 3+4: SMTP + Accept-All + Decisão ─────────────────────
    return _orchestrate_smtp(email, domain, mx_hosts)


def _orchestrate_smtp(email: str, domain: str, mx_hosts: list) -> Dict:
    """Orquestra probe real, probe randômico e decisão final."""
    from app.database import SessionLocal
    from app.services import domain_service, settings_service

    db = SessionLocal()
    try:
        smtp_from = settings_service.get_setting(db, "smtp_from_email", "verify@emailcheck.brazil")
        smtp_helo = settings_service.get_setting(db, "smtp_helo_hostname", "mail.emailcheck.local")
        provider = _fingerprint_provider(mx_hosts[0])

        # Cache de accept-all por domínio
        if domain_service.check_accept_all_cache(db, domain):
            return {
                "status": EmailStatus.ACCEPT_ALL,
                "reason": "Server is catch-all (cached)",
                "normalized_reason": "catch_all_detected",
                "technical_status": "CATCH_ALL_CACHED",
                "confidence_score": 95,
                "provider": provider,
                "technical_failure": False,
                "retryable": False,
                "policy_block": False,
                "accept_all_score": 1.0,
            }

        # ── Probe do endereço real ──────────────────────────────────
        real_outcome = None
        used_mx = None
        for mx_host in mx_hosts[:2]:
            real_outcome = _smtp_probe_with_retry(
                email, mx_host, 25, smtp_from, smtp_helo
            )
            used_mx = mx_host
            # Se temos um resultado definitivo (aceito ou rejeição clara), paramos
            if real_outcome.outcome_type in ("accepted", "invalid_recipient"):
                break
            # Se falha técnica/policy, tentamos o próximo MX
            if real_outcome.technical_failure or real_outcome.policy_block:
                continue
            # Ambíguo → paramos também (não adianta tentar outro MX)
            break

        if real_outcome is None:
            return {
                "status": EmailStatus.UNKNOWN,
                "reason": "No MX host responded",
                "normalized_reason": "smtp_connection_error",
                "technical_status": "SMTP_ALL_FAILED",
                "confidence_score": 0,
                "provider": provider,
                "technical_failure": True,
                "retryable": True,
                "policy_block": False,
                "accept_all_score": 0.0,
            }

        # ── Decisão rápida se não precisa de accept-all ─────────────
        # Se real NÃO foi aceito, decidimos direto (sem probe randômico)
        if real_outcome.outcome_type != "accepted":
            result = decide_final_status(real_outcome, provider=provider)
            result["provider"] = provider
            return result

        # ── Probe randômico para detecção de catch-all ──────────────
        fake_email = f"__probe__{uuid.uuid4().hex[:12]}__@{domain}"
        random_outcome = _smtp_probe_with_retry(
            fake_email, used_mx, 25, smtp_from, smtp_helo, max_retries=1
        )

        accept_all_info = detect_accept_all_behavior(real_outcome, random_outcome)

        # Atualiza cache de accept-all no domínio
        if accept_all_info.accept_all_reason == "catch_all_detected":
            domain_service.set_accept_all(db, domain, True)
        elif accept_all_info.accept_all_reason == "not_catch_all":
            domain_service.set_accept_all(db, domain, False)

        # ── Decisão final ───────────────────────────────────────────
        result = decide_final_status(real_outcome, accept_all_info, provider)
        result["provider"] = provider
        return result

    finally:
        db.close()
