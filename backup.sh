#!/bin/sh
# Daily consistent copy of the ZyroGuard SQLite database, kept for 14 days.
# Installed on the host as /opt/zyroguard/backup.sh, run by cron at 03:15.
set -e
docker exec zyroguard python -c "import sqlite3; s=sqlite3.connect('/data/zyroguard.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close(); s.close()"
docker cp zyroguard:/data/backup.db /opt/zyroguard/backup/zyroguard-$(date +%F).db
docker exec zyroguard rm -f /data/backup.db
chmod 600 /opt/zyroguard/backup/zyroguard-*.db
find /opt/zyroguard/backup -name "zyroguard-*.db" -mtime +14 -delete
