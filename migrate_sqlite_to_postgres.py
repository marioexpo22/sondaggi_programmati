#!/usr/bin/env python3
"""migrate_sqlite_to_postgres.py

Usage: Set environment variable DATABASE_URL to your Postgres URL (Railway provides it).
Place the sqlite file (default polls.db) in the same folder and run:

    DATABASE_URL=postgresql://... python migrate_sqlite_to_postgres.py --sqlite polls.db

This script will:
- create the target tables if not present
- copy rows from sqlite polls table to postgres polls table
- preserve fields: id, chat_id, question, options, interval_minutes, schedule_times, pinned, last_sent, last_message_id, delete_previous, active, creator_id

BE CAREFUL: run on a copy of your DB first. This script does not remove data from sqlite.
"""

import os, argparse, json, sqlite3
from urllib.parse import urlparse

try:
    import psycopg2
except ImportError:
    raise SystemExit('psycopg2 not installed. Install psycopg2-binary in your environment.')

def create_postgres_tables(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS polls (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        interval_minutes INTEGER,
        schedule_times TEXT,
        pinned BOOLEAN DEFAULT FALSE,
        last_sent BIGINT DEFAULT 0,
        last_message_id BIGINT DEFAULT NULL,
        delete_previous BOOLEAN DEFAULT FALSE,
        active BOOLEAN DEFAULT TRUE,
        creator_id BIGINT
    )
    """)
    conn.commit()

def migrate(sqlite_path, pg_dsn):
    sconn = sqlite3.connect(sqlite_path)
    scur = sconn.cursor()
    # Read all rows from sqlite polls table
    scur.execute("SELECT id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,last_message_id,delete_previous,active,creator_id FROM polls")
    rows = scur.fetchall()
    print(f"Found {len(rows)} rows in sqlite polls table.")
    pconn = psycopg2.connect(pg_dsn)
    create_postgres_tables(pconn)
    pcur = pconn.cursor()
    inserted = 0
    for r in rows:
        # Unpack row (sqlite may use 0/1 for booleans)
        (rid, chat_id, question, options, interval_minutes, schedule_times, pinned, last_sent, last_message_id, delete_previous, active, creator_id) = r
        pinned_bool = bool(pinned) if pinned is not None else False
        delete_prev_bool = bool(delete_previous) if delete_previous is not None else False
        pcur.execute("SELECT id FROM polls WHERE id=%s", (rid,))
        if pcur.fetchone():
            print(f"Skipping existing id {rid}")
            continue
        pcur.execute("""INSERT INTO polls (id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,last_message_id,delete_previous,active,creator_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""" ,
                     (rid, chat_id, question, options, interval_minutes, schedule_times, pinned_bool, last_sent, last_message_id, delete_prev_bool, active, creator_id))
        inserted += 1
    pconn.commit()
    pconn.close()
    sconn.close()
    print(f"Inserted {inserted} rows into Postgres.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sqlite', default='polls.db', help='Path to sqlite file (default polls.db)')
    args = parser.parse_args()
    pg = os.environ.get('DATABASE_URL')
    if not pg:
        raise SystemExit('Set DATABASE_URL environment variable to your Postgres DSN.')
    migrate(args.sqlite, pg)

if __name__ == '__main__':
    main()
