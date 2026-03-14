
import sys
import os

# Adiciona o diretório raiz ao path para importar o app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import engine, Base, SessionLocal
from app.models import EmailStatus
from app.verifier import verify_email
from app.services import settings_service

def test_verifier_logic():
    print("--- Iniciando Teste de Verificação ---")
    
    # Recria tabelas no banco de teste (se necessário)
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    # Configura identidade SMTP para o teste
    settings_service.set_setting(db, "smtp_from_email", "test-probe@meudominio.com.br")
    settings_service.set_setting(db, "smtp_helo_hostname", "probe.meudominio.com.br")
    
    email_to_test = "jonas.poli@gmail.com" # Um e-mail real para teste (ou use um mock se preferir)
    
    print(f"Verificando e-mail: {email_to_test}")
    result = verify_email(email_to_test)
    
    print("\nResultado da Verificação:")
    print(f"Status: {result.get('status')}")
    print(f"Reason: {result.get('reason')}")
    print(f"Technical Status: {result.get('technical_status')}")
    print(f"Confidence Score: {result.get('confidence_score')}")
    print(f"SMTP Code: {result.get('smtp_code')}")
    print(f"Provider: {result.get('provider')}")
    
    print("\n--- Teste de DNS Timeout (Simulado) ---")
    # Para testar isso de verdade precisaríamos de um mock de dns.resolver
    # Mas no código já vimos que dns.exception.Timeout retorna UNKNOWN.
    
    db.close()
    print("\n--- Teste Concluído ---")

if __name__ == "__main__":
    test_verifier_logic()
