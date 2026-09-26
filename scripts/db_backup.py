#!/usr/bin/env python3
from __future__ import annotations
import os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
# Minimal .env reader so backup works even before dependencies are installed.
env={}
p=ROOT/'.env'
if p.exists():
    for line in p.read_text(errors='ignore').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
raw=env.get('DB_PATH','bot.db')
src=Path(raw); src=src if src.is_absolute() else ROOT/src
if not src.exists():
    print(f'No database yet: {src}'); raise SystemExit(0)
outdir=src.parent/'backups'; outdir.mkdir(parents=True,exist_ok=True)
dest=outdir/f'preupdate_{datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")}.db'
a=sqlite3.connect(src,timeout=30); b=sqlite3.connect(dest,timeout=30)
try:
    a.execute('PRAGMA busy_timeout=30000'); a.backup(b); b.commit()
    r=b.execute('PRAGMA integrity_check').fetchone()
    if not r or str(r[0]).lower()!='ok': raise RuntimeError(f'integrity_check={r}')
finally:
    b.close(); a.close()
print(dest)
