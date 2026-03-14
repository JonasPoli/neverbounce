"""
test_verification_adjustments.py
--------------------------------
Testes unitários específicos para os 7 ajustes pontuais:
A) SMTP 251/252
B) Varredura de MX (até 4 hosts)
C) EHLO/MAIL FROM capture
D) Accept-all scoring (random ambiguous não promove)
E) Cache accept-all (tri-state)
F) Identity Fallback
G) Retries incrementados
"""
import sys
import os
import socket
from typing import Dict, Optional, List, Union
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.verifier import (
    normalize_smtp_outcome,
    detect_accept_all_behavior,
    decide_final_status,
    _orchestrate_smtp,
    SmtpOutcome,
    AcceptAllResult
)
from app.models import EmailStatus
from app.services import domain_service

def header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

passed = 0
total = 0

def assert_test(label, condition):
    global passed, total
    total += 1
    if condition:
        print(f"  ✅ {label}")
        passed += 1
    else:
        print(f"  ❌ {label}")

# ══════════════════════════════════════════════════════════════════════════════
# A) normalize_smtp_outcome (251/252)
# ══════════════════════════════════════════════════════════════════════════════
header("AJUSTE 1: SMTP 251 / 252")

o251 = normalize_smtp_outcome(251, "User not local; will forward")
assert_test("251 -> accepted", o251.outcome_type == "accepted")
assert_test("251 -> accepted_recipient", o251.normalized_reason == "accepted_recipient")

o252 = normalize_smtp_outcome(252, "Cannot VRFY user, but will accept")
assert_test("252 -> accepted", o252.outcome_type == "accepted")
assert_test("252 -> accepted_unverifiable", o252.normalized_reason == "accepted_unverifiable")

o250 = normalize_smtp_outcome(250, "OK")
assert_test("250 -> accepted_recipient", o250.normalized_reason == "accepted_recipient")

# ══════════════════════════════════════════════════════════════════════════════
# B) Varredura de MX & C) EHLO/MAIL FROM capture
# ══════════════════════════════════════════════════════════════════════════════
header("AJUSTE 2 & 3: Varredura MX (4 hosts) + EHLO/MAIL Capture")

@patch("app.verifier._smtp_probe_with_identity_fallback")
@patch("app.verifier.decide_final_status")
@patch("app.services.domain_service.get_accept_all_cache")
def test_mx_and_ehlo(mock_cache, mock_decide, mock_probe):
    mock_cache.return_value = None
    
    # Simulação 1: Primeiro MX ambiguous, segundo MX accepted
    # O loop deve continuar após o primeiro e parar no segundo.
    results = [
        SmtpOutcome(outcome_type="ambiguous", normalized_reason="smtp_ambiguous_rejection"),
        SmtpOutcome(outcome_type="accepted", normalized_reason="accepted_recipient")
    ]
    mock_probe.side_effect = results
    
    # Mock settings
    with patch("app.services.settings_service.get_setting", return_value="v@b.com"):
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            # _orchestrate_smtp(email, domain, mx_hosts)
            # Re-mocking results inside the function because of how side_effect works
            mock_probe.side_effect = results
            
            # Vamos testar a lógica de loop de MX em _orchestrate_smtp
            # Mocking resolve_mx to return 4 hosts
            mx_hosts = ["mx1", "mx2", "mx3", "mx4"]
            
            # Precisamos mockar o probe random também se o real for accepted
            mock_probe.side_effect = [results[0], results[1], SmtpOutcome(outcome_type="invalid_recipient")]
            
            from app.verifier import _orchestrate_smtp
            res = _orchestrate_smtp("test@example.com", "example.com", mx_hosts)
            
            assert_test("MX scan continuou após ambiguous", mock_probe.call_count >= 2)
            # Como o segundo foi accepted, ele deve ter parado a busca e feito o probe randômico
            # Total de probes esperados: 2 (real) + 1 (random fallback) = 3
            assert_test("MX scan parou no accepted", mock_probe.call_count == 3)
            
        finally:
            db.close()

test_mx_and_ehlo()

# ══════════════════════════════════════════════════════════════════════════════
# D) Accept-all scoring
# ══════════════════════════════════════════════════════════════════════════════
header("AJUSTE 4: Random Ambiguous NÃO promove para ACCEPT_ALL")

real_ok = SmtpOutcome(outcome_type="accepted")
rand_ambig = SmtpOutcome(outcome_type="ambiguous")
aa = detect_accept_all_behavior(real_ok, rand_ambig)
assert_test("Real OK + Rand Ambig -> Inconclusive/Not detectable", aa.accept_all_reason != "catch_all_detected")
assert_test("  score < 0.7", aa.accept_all_score < 0.7)

# Decide final status test
res = decide_final_status(real_ok, aa)
assert_test("Random ambiguous -> VALID (não promove)", res["status"] == EmailStatus.VALID)

# ══════════════════════════════════════════════════════════════════════════════
# E) Cache accept-all (tri-state)
# ══════════════════════════════════════════════════════════════════════════════
header("AJUSTE 5: Cache Tri-state")

@patch("app.services.domain_service.get_accept_all_cache")
@patch("app.verifier._smtp_probe_with_identity_fallback")
def test_cache_fallback(mock_probe, mock_cache):
    # Caso 1: Cache False (NOT catch-all) -> VALID sem probe randômico
    mock_cache.return_value = False
    mock_probe.return_value = SmtpOutcome(outcome_type="accepted") # real probe
    
    with patch("app.services.settings_service.get_setting", return_value="v@b.com"):
        from app.verifier import _orchestrate_smtp
        res = _orchestrate_smtp("test@example.com", "example.com", ["mx1"])
        assert_test("Cache False + Real OK -> VALID", res["status"] == EmailStatus.VALID)
        # Deve ter chamado probe apenas 1 vez (o real)
        assert_test("  Não fez probe randômico", mock_probe.call_count == 1)

    # Caso 2: Cache True (CATCH-ALL) -> ACCEPT_ALL imediato
    mock_probe.reset_mock()
    mock_cache.return_value = True
    with patch("app.services.settings_service.get_setting", return_value="v@b.com"):
        res = _orchestrate_smtp("test@example.com", "example.com", ["mx1"])
        assert_test("Cache True -> ACCEPT_ALL (cached)", res["status"] == EmailStatus.ACCEPT_ALL)
        assert_test("  Não fez nenhum probe", mock_probe.call_count == 0)

test_cache_fallback()

# ══════════════════════════════════════════════════════════════════════════════
# F) Identity Fallback
# ══════════════════════════════════════════════════════════════════════════════
header("AJUSTE 7: SMTP Identity Fallback")

@patch("app.verifier._smtp_probe_with_retry")
def test_identity_fallback(mock_retry):
    from app.verifier import _smtp_probe_with_identity_fallback
    
    # Simulação: Primeira identidade gera policy_block, segunda gera accepted
    mock_retry.side_effect = [
        SmtpOutcome(outcome_type="policy_block", policy_block=True),
        SmtpOutcome(outcome_type="accepted")
    ]
    
    froms = ["f1@a.com", "f2@a.com"]
    helos = ["h1.a.com", "h2.a.com"]
    
    out = _smtp_probe_with_identity_fallback("t@v.com", "mx1", 25, froms, helos)
    assert_test("Usou segunda identidade após bloqueio", mock_retry.call_count == 2)
    assert_test("Resultado final accepted", out.outcome_type == "accepted")

test_identity_fallback()

# ══════════════════════════════════════════════════════════════════════════════
# RESULTADO FINAL
# ══════════════════════════════════════════════════════════════════════════════
header("RESULTADO FINAL")
print(f"  {passed}/{total} testes passaram.\n")
sys.exit(0 if passed == total else 1)
