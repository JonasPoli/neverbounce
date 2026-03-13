"""
database.py
-----------
Configura o engine SQLAlchemy, a sessão local e a base declarativa.
Organizado para facilitar futura migração para PostgreSQL.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Caminho do banco de dados SQLite local
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'database.db')}"

# Engine com check_same_thread=False necessário para SQLite + FastAPI
# timeout em 30s evita 'database is locked' em picos de paralelismo
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False,
)

# Habilita modo WAL (Write-Ahead Logging) para melhor concorrência
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

# Fábrica de sessões
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base declarativa compartilhada por todos os modelos
Base = declarative_base()


def get_db():
    """
    Dependência FastAPI: abre uma sessão de banco de dados por request
    e garante fechamento ao final.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Cria todas as tabelas no banco se ainda não existirem.
    Depois executa migrações para adicionar colunas novas a tabelas existentes.
    Chamada automaticamente ao iniciar a aplicação.
    """
    from app import models  # noqa: F401 — importa para registrar os modelos na Base
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations():
    """
    Migração manual de schema: adiciona colunas novas a tabelas já existentes.
    O SQLAlchemy create_all() não faz ALTER TABLE, então fazemos aqui via PRAGMA + ALTER.
    Seguro para rodar sempre — verifica se a coluna já existe antes de agir.
    """
    migrations = [
        # (tabela, coluna, definição SQL)
        ("lists", "workers", "INTEGER DEFAULT 5"),
    ]

    with engine.connect() as conn:
        for table, column, definition in migrations:
            # PRAGMA table_info retorna as colunas existentes
            result = conn.execute(
                __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
            )
            existing_columns = [row[1] for row in result.fetchall()]

            if column not in existing_columns:
                conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )
                )
                conn.commit()
                import logging
                logging.getLogger(__name__).info(
                    f"Migração aplicada: ALTER TABLE {table} ADD COLUMN {column}"
                )
