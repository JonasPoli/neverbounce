"""
models.py
---------
Modelos SQLAlchemy para as tabelas do banco de dados.
Contém GlobalCache, EmailList e ListItem com seus relacionamentos.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


# ──────────────────────────────────────────────
# Constantes de status reutilizadas no sistema
# ──────────────────────────────────────────────
class EmailStatus:
    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"
    ACCEPT_ALL = "ACCEPT_ALL"


class ListStatus:
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ──────────────────────────────────────────────
# Cache global de e-mails verificados
# ──────────────────────────────────────────────
class GlobalCache(Base):
    """
    Armazena o resultado de cada e-mail já verificado.
    Compartilhado entre todas as listas — evita retrabalho desnecessário.
    """
    __tablename__ = "global_cache"

    email = Column(String, primary_key=True, index=True)
    status = Column(String, nullable=False)          # VALID | INVALID | UNKNOWN | ACCEPT_ALL
    reason = Column(Text, nullable=True)             # Motivo técnico resumido
    last_checked = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Novos campos para profundidade analítica
    confidence_score = Column(Integer, default=0)    # 0 a 100
    technical_status = Column(String, nullable=True) # Ex: BLOCKED_IP, MAILBOX_NOT_FOUND
    smtp_code = Column(Integer, nullable=True)
    provider = Column(String, nullable=True)         # Ex: GMAIL, OUTLOOK

    def __repr__(self):
        return f"<GlobalCache email={self.email} status={self.status} score={self.confidence_score}>"


# ──────────────────────────────────────────────
# Lista de e-mails submetida pelo usuário
# ──────────────────────────────────────────────
class EmailList(Base):
    """
    Representa uma submissão de lista pelo usuário.
    Rastreia progresso e configurações de verificação.
    """
    __tablename__ = "lists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)                     # Nome do arquivo ou "Paste"
    total_emails = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    status = Column(String, default=ListStatus.PENDING)       # PENDING | PROCESSING | COMPLETED | FAILED
    force_check = Column(Boolean, default=False)              # True = ignorar cache global
    workers = Column(Integer, default=5)                      # N.º de workers paralelos (1–20)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relacionamento 1:N com itens da lista
    items = relationship("ListItem", back_populates="email_list", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<EmailList id={self.id} name={self.name} status={self.status}>"


# ──────────────────────────────────────────────
# Item individual de e-mail dentro de uma lista
# ──────────────────────────────────────────────
class ListItem(Base):
    """
    Representa cada e-mail dentro de uma lista submetida.
    Armazena resultado individual da verificação.
    """
    __tablename__ = "list_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    list_id = Column(Integer, ForeignKey("lists.id"), nullable=False, index=True)
    email = Column(String, nullable=False, index=True)
    status = Column(String, nullable=True)             # Preenchido após verificação
    reason = Column(Text, nullable=True)
    checked_at = Column(DateTime, nullable=True)

    # Novos campos para profundidade analítica (espelham o cache)
    confidence_score = Column(Integer, default=0)
    technical_status = Column(String, nullable=True)
    smtp_code = Column(Integer, nullable=True)
    provider = Column(String, nullable=True)

    # Relacionamento N:1 com a lista
    email_list = relationship("EmailList", back_populates="items")

    def __repr__(self):
        return f"<ListItem email={self.email} status={self.status} score={self.confidence_score}>"


# ──────────────────────────────────────────────
# Configurações globais do sistema
# ──────────────────────────────────────────────
class SystemSetting(Base):
    """
    Armazena chaves e valores de configuração global.
    Ex: 'workers_count': '10'
    """
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<SystemSetting key={self.key} value={self.value}>"


# ──────────────────────────────────────────────
# Estatísticas e Cooldown de Domínios
# ──────────────────────────────────────────────
class DomainStat(Base):
    """
    Rastreia a última vez que um domínio foi contatado via SMTP.
    Evita que o sistema seja banido por excesso de requisições.
    """
    __tablename__ = "domain_stats"

    domain = Column(String, primary_key=True)
    last_contact = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Cache de Accept-All por domínio
    is_accept_all = Column(Boolean, default=False)
    accept_all_checked_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<DomainStat domain={self.domain} last_contact={self.last_contact} accept_all={self.is_accept_all}>"
