#!/usr/bin/env python3
"""Nightly SQLite backup. Keeps the newest 14 copies."""
import os
import sqlite3
import time

DB_PATH = os.environ.get("PACKET_DB", "/var/lib/packet/packet.db")
OUT_DIR = os.path.join(os.path.dirname(DB_PATH), "backups")
KEEP = 14

os.makedirs(OUT_DIR, exist_ok=True)
dest = os.path.join(OUT_DIR, "packet-%s.db" % time.strftime("%Y%m%d-%H%M%S"))
src = sqlite3.connect(DB_PATH)
dst = sqlite3.connect(dest)
src.backup(dst)
dst.close()
src.close()
for old in sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".db"))[:-KEEP]:
    os.remove(os.path.join(OUT_DIR, old))
print("backup written: " + dest)
