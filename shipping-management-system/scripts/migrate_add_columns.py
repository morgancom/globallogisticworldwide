"""
Idempotent migration script for SQLite to add simulation and hold location columns.
Usage: python scripts/migrate_add_columns.py

This script reads the SQLAlchemy DB URI from `config.Config.SQLALCHEMY_DATABASE_URI`.
It only supports SQLite URIs (sqlite://...). If you use another DB, run appropriate migrations.

It will add these columns if missing:
- packages.simulation_active INTEGER DEFAULT 0
- packages.simulation_speed_kmh REAL DEFAULT 60.0
- packages.simulation_interval INTEGER DEFAULT 5
- packages.simulation_started_at DATETIME
- hold_records.hold_lat REAL
- hold_records.hold_lng REAL
- hold_records.hold_location_name TEXT

The script is safe to run multiple times.
"""
import os
import sqlite3
import sys
from config import Config


def get_sqlite_path(uri: str):
    # Expect formats like sqlite:///relative.db or sqlite:///C:/abs/path.db
    if uri.startswith('sqlite:///'):
        return uri.replace('sqlite:///', '', 1)
    if uri.startswith('sqlite://'):
        return uri.replace('sqlite://', '', 1)
    return uri


def column_exists(conn, table, column):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info('%s')" % table)
    cols = [row[1] for row in cur.fetchall()]
    return column in cols


def add_column(conn, table, column_sql):
    cur = conn.cursor()
    cur.execute(column_sql)
    conn.commit()


def main():
    uri = getattr(Config, 'SQLALCHEMY_DATABASE_URI', None)
    if not uri:
        print('No SQLALCHEMY_DATABASE_URI found in Config')
        sys.exit(1)

    if not uri.startswith('sqlite'):
        print('This migration helper currently only supports SQLite URIs.\nDetected:', uri)
        sys.exit(1)

    db_path = get_sqlite_path(uri)
    db_path = os.path.abspath(db_path)

    if not os.path.exists(db_path):
        print(f"SQLite DB not found at {db_path}. If you are using a different DB, run migrations there.")
        sys.exit(1)

    print('Using SQLite DB:', db_path)

    conn = sqlite3.connect(db_path)

    try:
        # packages table columns
        pkg_table = 'packages'
        pkg_columns = [
            ("simulation_active", "ALTER TABLE packages ADD COLUMN simulation_active INTEGER DEFAULT 0"),
            ("simulation_speed_kmh", "ALTER TABLE packages ADD COLUMN simulation_speed_kmh REAL DEFAULT 60.0"),
            ("simulation_interval", "ALTER TABLE packages ADD COLUMN simulation_interval INTEGER DEFAULT 5"),
            ("simulation_started_at", "ALTER TABLE packages ADD COLUMN simulation_started_at DATETIME")
        ]

        for col, sql in pkg_columns:
            if column_exists(conn, pkg_table, col):
                print(f"Column {pkg_table}.{col} already exists — skipping")
            else:
                print(f"Adding column {pkg_table}.{col}...")
                add_column(conn, pkg_table, sql)
                print(f"Added {pkg_table}.{col}")

        # hold_records table columns
        hold_table = 'hold_records'
        hold_columns = [
            ("hold_lat", "ALTER TABLE hold_records ADD COLUMN hold_lat REAL"),
            ("hold_lng", "ALTER TABLE hold_records ADD COLUMN hold_lng REAL"),
            ("hold_location_name", "ALTER TABLE hold_records ADD COLUMN hold_location_name TEXT")
        ]

        for col, sql in hold_columns:
            if column_exists(conn, hold_table, col):
                print(f"Column {hold_table}.{col} already exists — skipping")
            else:
                print(f"Adding column {hold_table}.{col}...")
                add_column(conn, hold_table, sql)
                print(f"Added {hold_table}.{col}")

        print('\nMigration complete.')
    except Exception as e:
        print('Error during migration:', str(e))
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
