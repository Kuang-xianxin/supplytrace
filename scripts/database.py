"""Consistent online backup and restore-to-new-directory; never overwrites a DB."""
import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('Destination already exists; choose a new path')
    if not source.is_file():
        raise ValueError('Source database missing')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        integrity = dst.execute('PRAGMA integrity_check').fetchone()[0]
        counts={r[0]:dst.execute('SELECT count(*) FROM "'+r[0]+'"').fetchone()[0]
                for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if integrity != 'ok':
        raise ValueError('Integrity check failed; artifact retained')
    with destination.open('rb') as stream:
        checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'path':str(destination),'integrity_check':integrity,'counts':counts,'sha256':checksum}

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('operation',choices=['backup','restore']);p.add_argument('source');p.add_argument('destination');a=p.parse_args()
    print(json.dumps(backup(a.source,a.destination),ensure_ascii=False,indent=2))
