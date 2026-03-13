from sqlalchemy.orm import Session
from app.models import SystemSetting

# Cache em memória para evitar hits constantes no DB em loops de verificação
_SETTINGS_CACHE = {}

def get_setting(db: Session, key: str, default: str = None) -> str:
    """Retorna o valor de uma configuração, com cache em memória."""
    if key in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[key]
    
    setting = db.query(SystemSetting).filter_by(key=key).first()
    if setting:
        _SETTINGS_CACHE[key] = setting.value
        return setting.value
    
    return default

def set_setting(db: Session, key: str, value: str):
    """Atualiza uma configuração e o cache."""
    setting = db.query(SystemSetting).filter_by(key=key).first()
    if not setting:
        setting = SystemSetting(key=key, value=str(value))
        db.add(setting)
    else:
        setting.value = str(value)
    
    db.commit()
    _SETTINGS_CACHE[key] = str(value)

def get_workers_count(db: Session) -> int:
    """Helper para pegar o número de workers (default 5)."""
    val = get_setting(db, "workers_count", "5")
    try:
        return int(val)
    except:
        return 5

def get_domain_cooldown(db: Session) -> float:
    """Helper para pegar o tempo de cooldown (default 1.5s)."""
    val = get_setting(db, "domain_cooldown", "1.5")
    try:
        return float(val)
    except:
        return 1.5
