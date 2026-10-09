#!/usr/bin/env python3
"""
One-off migration to sanitize legacy/invalid package `status` values.

Usage:
  python scripts/sanitize_package_statuses.py --dry-run
  python scripts/sanitize_package_statuses.py --yes

This script updates rows in the `packages` table where `status` is NULL
or not in the accepted list and sets them to `in_transit`.

It uses the application's Flask app context and SQLAlchemy engine so it
works with the configured database (SQLite, MySQL, etc.).
"""
import argparse
import sys
import os

# Add the parent directory to the Python path so we can import app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from sqlalchemy import text

# Whitelist of allowed status values. Adjust if your app uses different values.
ALLOWED_STATUSES = [
    'created',
    'pending',
    'in_transit',
    'delivered',
    'on_hold',
    'cancelled',
    'returned'
]

def find_invalid_statuses():
    placeholders = ','.join("'{}'".format(s) for s in ALLOWED_STATUSES)
    sql = f"SELECT status, COUNT(*) as cnt FROM packages WHERE status IS NULL OR status NOT IN ({placeholders}) GROUP BY status"
    return list(db.session.execute(text(sql)))

def count_invalid():
    placeholders = ','.join("'{}'".format(s) for s in ALLOWED_STATUSES)
    sql = f"SELECT COUNT(*) FROM packages WHERE status IS NULL OR status NOT IN ({placeholders})"
    return db.session.execute(text(sql)).scalar()

def apply_fix():
    placeholders = ','.join("'{}'".format(s) for s in ALLOWED_STATUSES)
    sql = f"UPDATE packages SET status='in_transit' WHERE status IS NULL OR status NOT IN ({placeholders})"
    result = db.session.execute(text(sql))
    db.session.commit()
    # rowcount is DB/driver dependent; best-effort return
    try:
        return result.rowcount
    except Exception:
        return None

def main():
    parser = argparse.ArgumentParser(description='Sanitize package status values (one-off)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be changed and exit')
    parser.add_argument('--yes', action='store_true', help='Apply changes without prompting')
    args = parser.parse_args()

    with app.app_context():
        invalid = find_invalid_statuses()
        total = count_invalid() or 0

        if not invalid:
            print('No invalid or unknown package status values found. Nothing to do.')
            return

        print('Found the following status values that will be normalized to "in_transit":')
        for row in invalid:
            status, cnt = row[0], row[1]
            label = '<NULL>' if status is None else status
            print(f' - {label}: {cnt} package(s)')

        print(f"Total packages to update: {total}\n")

        if args.dry_run:
            print('Dry-run complete. No changes applied.')
            return

        if not args.yes:
            confirm = input('Proceed and set these statuses to "in_transit"? (y/N): ').strip().lower()
            if confirm not in ('y', 'yes'):
                print('Aborted by user. No changes applied.')
                return

        updated = apply_fix()
        if updated is None:
            print('Update executed. Row-count not available for this DB/driver.')
        else:
            print(f'Updated {updated} package(s) to "in_transit".')


if __name__ == '__main__':
    main()
