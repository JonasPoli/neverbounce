
import sqlite3
import os

db_path = "/Volumes/Dados/work/neverbounce/email-validator-pro/database.db"

def migrate():
    if not os.path.exists(db_path):
        print(f"Banco de dados não encontrado em {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        print("Adicionando colunas à tabela global_cache...")
        cursor.execute("ALTER TABLE global_cache ADD COLUMN confidence_score INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE global_cache ADD COLUMN technical_status TEXT")
        cursor.execute("ALTER TABLE global_cache ADD COLUMN smtp_code INTEGER")
        cursor.execute("ALTER TABLE global_cache ADD COLUMN provider TEXT")
    except sqlite3.OperationalError as e:
        print(f"Aviso global_cache: {e}")

    try:
        print("Adicionando colunas à tabela list_items...")
        cursor.execute("ALTER TABLE list_items ADD COLUMN confidence_score INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE list_items ADD COLUMN technical_status TEXT")
        cursor.execute("ALTER TABLE list_items ADD COLUMN smtp_code INTEGER")
        cursor.execute("ALTER TABLE list_items ADD COLUMN provider TEXT")
    except sqlite3.OperationalError as e:
        print(f"Aviso list_items: {e}")

    try:
        print("Adicionando colunas à tabela domain_stats...")
        cursor.execute("ALTER TABLE domain_stats ADD COLUMN is_accept_all BOOLEAN DEFAULT 0")
        cursor.execute("ALTER TABLE domain_stats ADD COLUMN accept_all_checked_at DATETIME")
    except sqlite3.OperationalError as e:
        print(f"Aviso domain_stats: {e}")

    conn.commit()
    conn.close()
    print("Migração concluída.")

if __name__ == "__main__":
    migrate()
