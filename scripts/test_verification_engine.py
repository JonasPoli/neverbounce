"""
test_verification_engine.py
----------------------------
Suite de testes técnicos obrigatórios para as 3 camadas:
  A) normalize_smtp_outcome
  B) detect_accept_all_behavior
  C) decide_final_status
"""
import sys
import os
import socket

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.verifier import (
    normalize_smtp_outcome,
    detect_accept_all_behavior,
    decide_final_status,
    should_retry,
    is_definitive_invalid,
    SmtpOutcome,
)
from app.models import EmailStatus


def header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check(label, condition):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
    return condition


passed = 0
total = 0


def assert_test(label, condition):
    global passed, total
    total += 1
    if check(label, condition):
        passed += 1


# ══════════════════════════════════════════════════════════════════════════════
# LAYER A: normalize_smtp_outcome
# ══════════════════════════════════════════════════════════════════════════════

header("CAMADA A: normalize_smtp_outcome")

# ── INVALID definitivos ──────────────────────────────────────────
print("\n  --- INVALID definitivos ---")

o = normalize_smtp_outcome(550, "5.1.1 User unknown")
assert_test("550 'User unknown' → invalid_recipient", o.outcome_type == "invalid_recipient")
assert_test("  recipient_rejected=True", o.recipient_rejected is True)
assert_test("  policy_block=False", o.policy_block is False)

o = normalize_smtp_outcome(550, "Mailbox does not exist")
assert_test("550 'Mailbox does not exist' → invalid_recipient", o.outcome_type == "invalid_recipient")

o = normalize_smtp_outcome(550, "No such user here")
assert_test("550 'No such user here' → invalid_recipient", o.outcome_type == "invalid_recipient")

o = normalize_smtp_outcome(553, "Invalid recipient")
assert_test("553 'Invalid recipient' → invalid_recipient", o.outcome_type == "invalid_recipient")

o = normalize_smtp_outcome(550, "Unknown local part")
assert_test("550 'Unknown local part' → invalid_recipient", o.outcome_type == "invalid_recipient")

# ── POLICY BLOCK → outcome NÃO é invalid ─────────────────────────
print("\n  --- POLICY BLOCK → UNKNOWN ---")

o = normalize_smtp_outcome(554, "IP blacklisted by Spamhaus")
assert_test("554 'Spamhaus' → policy_block", o.outcome_type == "policy_block")
assert_test("  policy_block=True", o.policy_block is True)
assert_test("  recipient_rejected=False", o.recipient_rejected is False)

o = normalize_smtp_outcome(550, "Client host blocked using RBL")
assert_test("550 'Client host blocked' → policy_block", o.outcome_type == "policy_block")

o = normalize_smtp_outcome(550, "Access denied - reputation too low")
assert_test("550 'Access denied' → policy_block", o.outcome_type == "policy_block")

o = normalize_smtp_outcome(554, "Denied by policy")
assert_test("554 'Denied by policy' → policy_block", o.outcome_type == "policy_block")

o = normalize_smtp_outcome(550, "Sender address rejected: not owned by user")
assert_test("550 'Sender address rejected' → policy_block", o.outcome_type == "policy_block")

o = normalize_smtp_outcome(550, "Bad HELO - rejected")
assert_test("550 'Bad HELO' → policy_block", o.outcome_type == "policy_block")

o = normalize_smtp_outcome(550, "PTR record mismatch")
assert_test("550 'PTR record' → policy_block", o.outcome_type == "policy_block")

o = normalize_smtp_outcome(550, "Rejected by filter")
assert_test("550 'Rejected by filter' → policy_block", o.outcome_type == "policy_block")

# ── TECHNICAL FAILURE ─────────────────────────────────────────────
print("\n  --- TECHNICAL FAILURE ---")

o = normalize_smtp_outcome(exception=socket.timeout())
assert_test("socket.timeout → technical_failure", o.outcome_type == "technical_failure")
assert_test("  technical_failure=True", o.technical_failure is True)
assert_test("  retryable=True", o.retryable is True)

o = normalize_smtp_outcome(exception=ConnectionResetError("Connection reset"))
assert_test("ConnectionReset → technical_failure", o.outcome_type == "technical_failure")

o = normalize_smtp_outcome(exception=OSError("Network is unreachable"))
assert_test("Network unreachable → technical_failure", o.outcome_type == "technical_failure")

# ── TEMPORARY / GREYLIST ──────────────────────────────────────────
print("\n  --- TEMPORARY / GREYLIST ---")

o = normalize_smtp_outcome(421, "Too many connections from your IP")
assert_test("421 'Too many connections' → temporary_failure", o.outcome_type == "temporary_failure")
assert_test("  retryable=True", o.retryable is True)

o = normalize_smtp_outcome(450, "Greylisted, try again later")
assert_test("450 'Greylisted' → temporary_failure", o.outcome_type == "temporary_failure")

o = normalize_smtp_outcome(451, "Temporarily deferred")
assert_test("451 'Temporarily deferred' → temporary_failure", o.outcome_type == "temporary_failure")

# ── ACCEPTED ──────────────────────────────────────────────────────
print("\n  --- ACCEPTED ---")

o = normalize_smtp_outcome(250, "2.1.5 OK")
assert_test("250 'OK' → accepted", o.outcome_type == "accepted")
assert_test("  accept_hint=True", o.accept_hint is True)

# ── AMBIGUOUS (5xx sem match) ─────────────────────────────────────
print("\n  --- AMBIGUOUS ---")

o = normalize_smtp_outcome(550, "Administrative rejection")
assert_test("550 'Administrative rejection' → ambiguous", o.outcome_type == "ambiguous")
assert_test("  recipient_rejected=False", o.recipient_rejected is False)
assert_test("  policy_block=False", o.policy_block is False)

o = normalize_smtp_outcome(500, "Syntax error")
assert_test("500 'Syntax error' → ambiguous", o.outcome_type == "ambiguous")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

header("HELPERS: should_retry / is_definitive_invalid")

o_temporal = normalize_smtp_outcome(451, "Try again later")
assert_test("should_retry(451 greylist) = True", should_retry(o_temporal) is True)

o_invalid = normalize_smtp_outcome(550, "User unknown")
assert_test("should_retry(550 user unknown) = False", should_retry(o_invalid) is False)

o_accepted = normalize_smtp_outcome(250, "OK")
assert_test("should_retry(250 OK) = False", should_retry(o_accepted) is False)

assert_test("is_definitive_invalid(user unknown) = True", is_definitive_invalid(o_invalid) is True)

o_policy = normalize_smtp_outcome(550, "Client host blocked")
assert_test("is_definitive_invalid(policy block) = False", is_definitive_invalid(o_policy) is False)

o_ambig = normalize_smtp_outcome(550, "Administrative rejection")
assert_test("is_definitive_invalid(ambiguous) = False", is_definitive_invalid(o_ambig) is False)


# ══════════════════════════════════════════════════════════════════════════════
# LAYER B: detect_accept_all_behavior
# ══════════════════════════════════════════════════════════════════════════════

header("CAMADA B: detect_accept_all_behavior")

# Caso A: real aceito, random aceito → catch-all
real_ok = normalize_smtp_outcome(250, "OK")
rand_ok = normalize_smtp_outcome(250, "OK")
aa = detect_accept_all_behavior(real_ok, rand_ok)
assert_test("Real OK + Random OK → score >= 0.7", aa.accept_all_score >= 0.7)
assert_test("  reason = catch_all_detected", aa.accept_all_reason == "catch_all_detected")
assert_test("  technical_contamination=False", aa.technical_contamination is False)

# Caso C: real aceito, random rejeitado → NOT catch-all
real_ok = normalize_smtp_outcome(250, "OK")
rand_invalid = normalize_smtp_outcome(550, "User unknown")
aa = detect_accept_all_behavior(real_ok, rand_invalid)
assert_test("Real OK + Random invalid → not catch-all", aa.accept_all_reason == "not_catch_all")

# Caso D: real com falha técnica → contaminado
real_fail = normalize_smtp_outcome(exception=socket.timeout())
rand_ok = normalize_smtp_outcome(250, "OK")
aa = detect_accept_all_behavior(real_fail, rand_ok)
assert_test("Real timeout + Random OK → contaminated", aa.technical_contamination is True)

# Caso E: ambos com policy_block → contaminado
real_block = normalize_smtp_outcome(554, "IP blacklisted by Spamhaus")
rand_block = normalize_smtp_outcome(554, "IP blocked")
aa = detect_accept_all_behavior(real_block, rand_block)
assert_test("Ambos blocked → contaminated", aa.technical_contamination is True)

# Caso F: real OK, random com falha técnica → contaminado
real_ok = normalize_smtp_outcome(250, "OK")
rand_fail = normalize_smtp_outcome(exception=socket.timeout())
aa = detect_accept_all_behavior(real_ok, rand_fail)
assert_test("Real OK + Random timeout → contaminated", aa.technical_contamination is True)
assert_test("  score < 0.7 (penalidade)", aa.accept_all_score < 0.7)


# ══════════════════════════════════════════════════════════════════════════════
# LAYER C: decide_final_status
# ══════════════════════════════════════════════════════════════════════════════

header("CAMADA C: decide_final_status")

# 1. invalid_recipient definitivo → INVALID
o = normalize_smtp_outcome(550, "User unknown")
r = decide_final_status(o)
assert_test("User unknown → INVALID", r["status"] == EmailStatus.INVALID)
assert_test("  normalized_reason=invalid_recipient", r["normalized_reason"] == "invalid_recipient")

# 2. Policy block → UNKNOWN
o = normalize_smtp_outcome(554, "IP blacklisted by Spamhaus")
r = decide_final_status(o)
assert_test("IP blacklisted → UNKNOWN", r["status"] == EmailStatus.UNKNOWN)
assert_test("  policy_block=True", r["policy_block"] is True)

# 3. Technical failure → UNKNOWN
o = normalize_smtp_outcome(exception=socket.timeout())
r = decide_final_status(o)
assert_test("Timeout → UNKNOWN", r["status"] == EmailStatus.UNKNOWN)
assert_test("  technical_failure=True", r["technical_failure"] is True)
assert_test("  retryable=True", r["retryable"] is True)

# 4. Real aceito + random rejeição → VALID
real_ok = normalize_smtp_outcome(250, "OK")
rand_inv = normalize_smtp_outcome(550, "User unknown")
aa = detect_accept_all_behavior(real_ok, rand_inv)
r = decide_final_status(real_ok, aa)
assert_test("Real OK + Random invalid → VALID", r["status"] == EmailStatus.VALID)

# 5. Real aceito + random aceito → ACCEPT_ALL
real_ok = normalize_smtp_outcome(250, "OK")
rand_ok = normalize_smtp_outcome(250, "OK")
aa = detect_accept_all_behavior(real_ok, rand_ok)
r = decide_final_status(real_ok, aa)
assert_test("Real OK + Random OK → ACCEPT_ALL", r["status"] == EmailStatus.ACCEPT_ALL)

# 6. Real aceito + random timeout (contaminado) → VALID com score baixo
real_ok = normalize_smtp_outcome(250, "OK")
rand_fail = normalize_smtp_outcome(exception=socket.timeout())
aa = detect_accept_all_behavior(real_ok, rand_fail)
r = decide_final_status(real_ok, aa)
assert_test("Real OK + Random timeout → VALID (contaminated)", r["status"] == EmailStatus.VALID)
assert_test("  confidence < 90", r["confidence_score"] < 90)

# 7. Ambiguous 550 → UNKNOWN
o = normalize_smtp_outcome(550, "Administrative rejection")
r = decide_final_status(o)
assert_test("Ambiguous 550 → UNKNOWN", r["status"] == EmailStatus.UNKNOWN)

# 8. Greylisting → UNKNOWN com retryable=True
o = normalize_smtp_outcome(451, "Greylisted try again")
r = decide_final_status(o)
assert_test("Greylisting → UNKNOWN", r["status"] == EmailStatus.UNKNOWN)
assert_test("  retryable=True", r["retryable"] is True)


# ══════════════════════════════════════════════════════════════════════════════
# RESULTADO
# ══════════════════════════════════════════════════════════════════════════════

header("RESULTADO FINAL")
print(f"\n  {passed}/{total} testes passaram.\n")
sys.exit(0 if passed == total else 1)
