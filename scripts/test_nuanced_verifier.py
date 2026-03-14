
import sys
import os

# Adiciona o diretório raiz ao path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.verifier import _normalize_smtp_response
from app.models import EmailStatus

def test_normalization():
    print("--- Testando Normalização SMTP ---")
    
    cases = [
        # (code, message, expected_status, expected_tech)
        (250, "2.1.5 OK", EmailStatus.VALID, "MAILBOX_ACCEPTED"),
        
        # Erros Definitivos -> INVALID
        (550, "5.1.1 User unknown", EmailStatus.INVALID, "MAILBOX_NOT_FOUND"),
        (550, "No such user here", EmailStatus.INVALID, "MAILBOX_NOT_FOUND"),
        (550, "Mailbox unavailable", EmailStatus.INVALID, "MAILBOX_NOT_FOUND"),
        
        # Bloqueios / Política -> UNKNOWN
        (554, "IP blacklisted by Spamhaus", EmailStatus.UNKNOWN, "BLOCKED_OR_POLICY"),
        (550, "Access denied - reputation too low", EmailStatus.UNKNOWN, "BLOCKED_OR_POLICY"),
        (550, "Message rejected by filter", EmailStatus.UNKNOWN, "BLOCKED_OR_POLICY"),
        (554, "Denied by policy", EmailStatus.UNKNOWN, "BLOCKED_OR_POLICY"),
        
        # Ambiguidade -> UNKNOWN
        (550, "Administrative rejection", EmailStatus.UNKNOWN, "SMTP_UNCERTAIN_550"),
        (500, "Syntax error", EmailStatus.UNKNOWN, "SMTP_UNCERTAIN_500"),
        
        # Temporário -> retry
        (421, "Too many connections", "retry", None),
        (450, "Greylisted, try again later", "retry", None),
    ]
    
    passed = 0
    for i, (code, msg, exp_status, exp_tech) in enumerate(cases):
        res = _normalize_smtp_response(code, msg)
        
        actual_status = res if isinstance(res, str) else res.get("status")
        actual_tech = None if isinstance(res, str) else res.get("technical_status")
        
        if actual_status == exp_status and (exp_tech is None or actual_tech == exp_tech):
            print(f"✅ Caso {i+1}: {code} '{msg}' -> {actual_status} ({actual_tech})")
            passed += 1
        else:
            print(f"❌ Caso {i+1}: {code} '{msg}' -> Esperado {exp_status} ({exp_tech}), Obtido {actual_status} ({actual_tech})")
            
    print(f"\nResultado final: {passed}/{len(cases)} testes passaram.")
    return passed == len(cases)

if __name__ == "__main__":
    if test_normalization():
        sys.exit(0)
    else:
        sys.exit(1)
