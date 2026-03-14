
import sqlite3
import os

db_path = "/Volumes/Dados/work/neverbounce/email-validator-pro/database.db"

MIGRATIONS = [
    # global_cache
    ("global_cache", "confidence_score", "ALTER TABLE global_cache ADD COLUMN confidence_score INTEGER DEFAULT 0"),
    ("global_cache", "technical_status", "ALTER TABLE global_cache ADD COLUMN technical_status TEXT"),
    ("global_cache", "smtp_code", "ALTER TABLE global_cache ADD COLUMN smtp_code INTEGER"),
    ("global_cache", "provider", "ALTER TABLE global_cache ADD COLUMN provider TEXT"),
    ("global_cache", "normalized_reason", "ALTER TABLE global_cache ADD COLUMN normalized_reason TEXT"),
    ("global_cache", "technical_failure", "ALTER TABLE global_cache ADD COLUMN technical_failure BOOLEAN DEFAULT 0"),
    ("global_cache", "retryable", "ALTER TABLE global_cache ADD COLUMN retryable BOOLEAN DEFAULT 0"),
    ("global_cache", "policy_block", "ALTER TABLE global_cache ADD COLUMN policy_block BOOLEAN DEFAULT 0"),
    ("global_cache", "accept_all_score", "ALTER TABLE global_cache ADD COLUMN accept_all_score TEXT"),
    # list_items
    ("list_items", "confidence_score", "ALTER TABLE list_items ADD COLUMN confidence_score INTEGER DEFAULT 0"),
    ("list_items", "technical_status", "ALTER TABLE list_items ADD COLUMN technical_status TEXT"),
    ("list_items", "smtp_code", "ALTER TABLE list_items ADD COLUMN smtp_code INTEGER"),
    ("list_items", "provider", "ALTER TABLE list_items ADD COLUMN provider TEXT"),
    ("list_items", "normalized_reason", "ALTER TABLE list_items ADD COLUMN normalized_reason TEXT"),
    ("list_items", "technical_failure", "ALTER TABLE list_items ADD COLUMN technical_failure BOOLEAN DEFAULT 0"),
    ("list_items", "retryable", "ALTER TABLE list_items ADD COLUMN retryable BOOLEAN DEFAULT 0"),
    ("list_items", "policy_block", "ALTER TABLE list_items ADD COLUMN policy_block BOOLEAN DEFAULT 0"),
    ("list_items", "accept_all_score", "ALTER TABLE list_items ADD COLUMN accept_all_score TEXT"),
    # domain_stats
    ("domain_stats", "is_accept_all", "ALTER TABLE domain_stats ADD COLUMN is_accept_all BOOLEAN DEFAULT 0"),
    ("domain_stats", "accept_all_checked_at", "ALTER TABLE domain_stats ADD COLUMN accept_all_checked_at DATETIME"),
]


def migrate():
    if not os.path.exists(db_path):
        print(f"Banco de dados não encontrado em {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for table, column, sql in MIGRATIONS:
        try:
            cursor.execute(sql)
            print(f"  ✅ {table}.{column} adicionada")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  ⏭️  {table}.{column} já existe")
            else:
                print(f"  ❌ {table}.{column}: {e}")

    conn.commit()
    conn.close()
    print("\nMigração concluída.")


if __name__ == "__main__":
    migrate()
