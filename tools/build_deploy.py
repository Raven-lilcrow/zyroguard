"""Build one self-contained, guarded deploy script for the live bot (see deploy.md, "The guarded way").

    python tools/build_deploy.py <name> <changed file> [<changed file> ...] > deploy.sh
    ssh root@YOUR_SERVER "bash -s" < deploy.sh

<name> labels the backups (backup/zyroguard-<date>-pre-<name>.db, /opt/zyroguard/pre-<name>/).
The changed files are paths like zyroguard/assistant.py. The script carries the whole local package
(zyroguard/*.py, rules.yaml, requirements.txt, Dockerfile) and on the server aborts without changing
anything unless the ONLY files that differ from the live copies are the ones listed.
"""
import base64
import io
import re
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if len(sys.argv) < 3 or not re.fullmatch(r"[a-z0-9-]+", sys.argv[1]):
    sys.exit(__doc__)
name, expected = sys.argv[1], sys.argv[2:]
files = ["requirements.txt", "Dockerfile"] + sorted(
    p.relative_to(ROOT).as_posix() for p in (ROOT / "zyroguard").iterdir()
    if p.is_file() and p.suffix in (".py", ".yaml"))
for f in expected:
    if f not in files:
        sys.exit(f"{f} is not part of the bundle")

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    for f in files:
        data = (ROOT / f).read_bytes().replace(b"\r\n", b"\n")
        info = tarfile.TarInfo(f)
        info.size, info.mode = len(data), 0o644
        tar.addfile(info, io.BytesIO(data))
payload = base64.b64encode(buf.getvalue()).decode()

guard = r'''
NEW=/tmp/zgnew
cd /opt/zyroguard
EXPECTED=" @EXPECTED@ "
changed=""
for f in requirements.txt Dockerfile; do   # may differ only when named, e.g. a new dependency
  cmp -s <(tr -d '\r' < "$f") "$NEW/$f" || changed="$changed $f"
done
for f in $(cd "$NEW" && find zyroguard -type f \( -name '*.py' -o -name '*.yaml' \) | sort); do
  if [ ! -f "$f" ] || ! cmp -s <(tr -d '\r' < "$f") "$NEW/$f"; then changed="$changed $f"; fi
done
for f in $(find zyroguard -type f \( -name '*.py' -o -name '*.yaml' \) -not -path '*__pycache__*' | sort); do
  [ -f "$NEW/$f" ] || { echo "server has $f that the bundle lacks: aborting, nothing was changed"; rm -rf "$NEW"; exit 3; }
done
echo "differs from the server:$changed"
for f in $changed; do
  case "$EXPECTED" in *" $f "*) ;; *) echo "UNEXPECTED difference in $f: aborting, nothing was changed"; rm -rf "$NEW"; exit 3 ;; esac
done
[ -n "$changed" ] || { echo "nothing to deploy"; rm -rf "$NEW"; exit 0; }
echo "running image: $(docker inspect -f '{{.Image}}' zyroguard | cut -c8-19)"

echo "== backup"
docker exec zyroguard python -c "import sqlite3; s=sqlite3.connect('/data/zyroguard.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close(); s.close()"
docker cp zyroguard:/data/backup.db "/opt/zyroguard/backup/zyroguard-$(date +%F)-pre-@NAME@.db"
docker exec zyroguard rm -f /data/backup.db
chmod 600 /opt/zyroguard/backup/zyroguard-*.db
ls -la /opt/zyroguard/backup | tail -2
mkdir -p /opt/zyroguard/pre-@NAME@
: > /opt/zyroguard/pre-@NAME@/new-files.txt   # files the server didn't have: a rollback deletes these
for f in $changed; do
  if [ -f "$f" ]; then cp "$f" /opt/zyroguard/pre-@NAME@/; else echo "$f" >> /opt/zyroguard/pre-@NAME@/new-files.txt; fi
done
echo "saved the live copies of the changed files in /opt/zyroguard/pre-@NAME@ (new files: $(wc -l < /opt/zyroguard/pre-@NAME@/new-files.txt))"

echo "== tag rollback image"
docker tag zyroguard-zyroguard:latest zyroguard:prev
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}}' | grep zyroguard

echo "== copy"
for f in $changed; do cp "$NEW/$f" "$f"; echo "  $f"; done
echo "== build"
docker compose up -d --build 2>&1 | tail -4
echo "== wait for healthy"
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' zyroguard 2>/dev/null || echo none)
  [ "$s" = "healthy" ] && break
  sleep 5
done
echo "health: $s"
docker compose logs --tail=12 zyroguard 2>&1 | grep -v onnxruntime | cut -c1-200 | sed -E 's/bot[0-9]{6,}:[A-Za-z0-9_-]{20,}/bot<redacted>/g'
echo "== checks"
docker exec zyroguard python -c "import zyroguard.main; print('package imports fine')"
docker inspect -f 'restarts: {{.RestartCount}}, started {{.State.StartedAt}}' zyroguard
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' zyroguard
rm -rf "$NEW"
'''.replace("@EXPECTED@", " ".join(expected)).replace("@NAME@", name)

sys.stdout.buffer.write((   # bytes, so Windows does not turn the newlines into CRLF (bash would choke)
    "#!/bin/bash\n"
    f"# Guarded ZyroGuard deploy '{name}': {' '.join(expected)}\n"
    "set -e\n"
    "rm -rf /tmp/zgnew && mkdir -p /tmp/zgnew\n"
    "base64 -d > /tmp/zgnew.tgz <<'PAYLOAD'\n"
    + "\n".join(payload[i:i + 76] for i in range(0, len(payload), 76))
    + "\nPAYLOAD\n"
    "tar -xzf /tmp/zgnew.tgz -C /tmp/zgnew && rm -f /tmp/zgnew.tgz\n"
    + guard).encode())
