"""
Migrate local SQLite data to Render's PostgreSQL database.

Usage:
    DATABASE_URL="postgresql://..." python3 migrate_to_postgres.py
"""

import os
import sqlite3
import json
from datetime import datetime

# ── 1. Read from local SQLite ─────────────────────────────────────────────────
SQLITE_PATH = "data/antisemitism.db"

if not os.path.exists(SQLITE_PATH):
    print(f"ERROR: {SQLITE_PATH} not found. Run from the antisemitism-app folder.")
    exit(1)

conn = sqlite3.connect(SQLITE_PATH)
rows = conn.execute("""
    SELECT newspaper, date, category_scores, overall_rating,
           evidence, source_filename, methodology_version, processing_timestamp
    FROM newspaper_analyses
    ORDER BY date
""").fetchall()
conn.close()

print(f"Found {len(rows)} records in SQLite:")
for r in rows:
    print(f"  {r[0]} — {r[1]} — FRS {r[3]:.2f}")

# ── 2. Connect to PostgreSQL ──────────────────────────────────────────────────
pg_url = os.environ.get("DATABASE_URL", "")
if not pg_url:
    print("\nERROR: Set the DATABASE_URL environment variable first.")
    print("Example:")
    print('  DATABASE_URL="postgresql://user:pass@host/db" python3 migrate_to_postgres.py')
    exit(1)

if pg_url.startswith("postgres://"):
    pg_url = pg_url.replace("postgres://", "postgresql://", 1)

try:
    from sqlalchemy import create_engine, text
    engine = create_engine(pg_url)
except ImportError:
    print("ERROR: sqlalchemy not installed. Run: pip3 install sqlalchemy psycopg2-binary")
    exit(1)

# ── 3. Create table if needed ─────────────────────────────────────────────────
with engine.begin() as db:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS newspaper_analyses (
            id SERIAL PRIMARY KEY,
            newspaper VARCHAR(100) NOT NULL,
            date DATE NOT NULL,
            category_scores TEXT NOT NULL,
            overall_rating FLOAT NOT NULL,
            evidence TEXT,
            source_filename VARCHAR(255),
            methodology_version VARCHAR(50) DEFAULT '1.0',
            processing_timestamp TIMESTAMP DEFAULT NOW(),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            CONSTRAINT uq_newspaper_date UNIQUE (newspaper, date)
        )
    """))
    print("\nTable ready.")

# ── 4. Insert / upsert all records ────────────────────────────────────────────
inserted = 0
updated  = 0

with engine.begin() as db:
    for (newspaper, date, category_scores, overall_rating,
         evidence, source_filename, methodology_version, processing_timestamp) in rows:

        result = db.execute(text("""
            INSERT INTO newspaper_analyses
                (newspaper, date, category_scores, overall_rating,
                 evidence, source_filename, methodology_version, processing_timestamp)
            VALUES
                (:newspaper, :date, :category_scores, :overall_rating,
                 :evidence, :source_filename, :methodology_version, :processing_timestamp)
            ON CONFLICT (newspaper, date) DO UPDATE SET
                category_scores      = EXCLUDED.category_scores,
                overall_rating       = EXCLUDED.overall_rating,
                evidence             = EXCLUDED.evidence,
                source_filename      = EXCLUDED.source_filename,
                methodology_version  = EXCLUDED.methodology_version,
                processing_timestamp = EXCLUDED.processing_timestamp,
                updated_at           = NOW()
            RETURNING (xmax = 0) AS inserted
        """), {
            "newspaper":            newspaper,
            "date":                 date,
            "category_scores":      category_scores,
            "overall_rating":       overall_rating,
            "evidence":             evidence or "[]",
            "source_filename":      source_filename or "",
            "methodology_version":  methodology_version or "1.0",
            "processing_timestamp": processing_timestamp or datetime.utcnow().isoformat(),
        })

        was_inserted = result.fetchone()[0]
        if was_inserted:
            inserted += 1
        else:
            updated += 1

print(f"\nMigration complete: {inserted} inserted, {updated} updated.")
print("All historical data is now in PostgreSQL.")
