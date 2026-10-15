# Deploy to the server

Target: your server, written `YOUR_SERVER` in the commands below (keep its address in your password
manager, not in this repo). n8n already runs there under
docker compose with the Caddy container. ZyroGuard joins n8n's docker network so n8n can
reach `http://zyroguard:8080` and the bot can reach `http://n8n:5678`.

## First deploy

From this folder on Windows:

```bash
ssh root@YOUR_SERVER "mkdir -p /opt/zyroguard"
```

```bash
scp -r Dockerfile docker-compose.yml requirements.txt zyroguard root@YOUR_SERVER:/opt/zyroguard/
```

On the server, create the env file with tight permissions:

```bash
ssh root@YOUR_SERVER "cd /opt/zyroguard && touch .env && chmod 600 .env"
```

Put the values in `/opt/zyroguard/.env` (see `.env.example`). Never commit it, never scp it
from a folder that syncs to a cloud drive.

Check the network name n8n uses (the compose file assumes `n8n_default`):

```bash
ssh root@YOUR_SERVER "docker network ls | grep n8n"
```

If it is different, change `networks:` at the bottom of `docker-compose.yml`.

```bash
ssh root@YOUR_SERVER "cd /opt/zyroguard && docker compose up -d --build && docker compose logs --tail=50"
```

Expect `ZyroGuard 0.1.0 starting as @YourBot`, then `Run polling for bot @YourBot`. Now in Telegram:

1. `/diag` in the group. Every line a check mark, or follow the fix hint on the failed line.
2. `/test Hello, we are Royal Dynasty International, USDT holders earn 10% commission`
   should print the matched rules and the action it would take.
3. Watch the mod-log group for SHADOW entries for a few days, then `/mode enforce`.

## Update

The quick way, when you are sure nobody else changed the server:

```bash
scp -r zyroguard root@YOUR_SERVER:/opt/zyroguard/ && ssh root@YOUR_SERVER "cd /opt/zyroguard && docker compose up -d --build"
```

**The guarded way (used for every deploy since 2026-10-13).** More than one person or session
works on this bot, so a deploy must not overwrite a change someone else shipped. One script, run
in one ssh connection (`ssh root@YOUR_SERVER "bash -s" < deploy.sh`), carries the whole local
package as an embedded archive and on the server:

1. compares every file with the live copy (line endings ignored) and **aborts without changing
   anything** if any file other than the expected ones differs, or the server has a file the
   bundle lacks;
2. backs up the database to a separately named file, `backup/zyroguard-<date>-pre-<name>.db`
   (not `backup.sh`, which would overwrite today's nightly copy), and copies the live versions of
   the changed files to `/opt/zyroguard/pre-<name>/`;
3. tags the running image `zyroguard:prev`, copies the files, `docker compose up -d --build`;
4. waits for `healthy`, prints the last log lines, runs a few in-container checks of the new
   code, restart count and memory.

Build the script with `tools/build_deploy.py`, naming the deploy and the files you expect to change:

```bash
.venv/Scripts/python tools/build_deploy.py mention zyroguard/assistant.py zyroguard/main.py > deploy.sh
```

```bash
ssh root@YOUR_SERVER "bash -s" < deploy.sh
```

Rollback is the image tag below, or copying the files back from `pre-<name>/` and rebuilding.
`deploy.sh` holds the whole package; delete it after the deploy.

Then check the live version end to end (README: "End-to-end check of a new group" and "End-to-end
check of a full client setup"). Both run in a throwaway copy of the live image and never touch the
live database.

The server's sshd blocks an address after about 3 logins in quick succession, so do each check
or deploy in one ssh connection.

Rules-only change: scp `zyroguard/rules.yaml` (it is bind-mounted into the container), then `docker compose restart`.

## Backup

The SQLite file lives in the `zyroguard-data` volume.

```bash
ssh root@YOUR_SERVER "/opt/zyroguard/backup.sh && ls /opt/zyroguard/backup"
```

## n8n workflow

Import `n8n/ZyroGuard-Modlog.json`. Set these after import:

- In the **Config** node and in the `X-ZyroGuard-Secret` header of the **GET /stats** node, replace
  `CHANGE_ME` with the value of `N8N_EVENT_SECRET`. `/stats` lists every group, so the bot only
  answers it with that secret, and it is off when `N8N_EVENT_SECRET` is empty.
- In both Telegram nodes, pick your Telegram credential and put the mod-log chat id in the
  `MODLOG_CHAT_ID` field of the **Config** node.
- Activate the workflow.

The webhook path is `zyroguard-event`, so `N8N_EVENT_URL=http://n8n:5678/webhook/zyroguard-event`.
The daily digest calls `http://zyroguard:8080/stats?since=24h` at 09:00 Asia/Manila.
The bot already posts its own mod-log lines and digest directly, so the n8n workflow is a
second copy you can extend (Slack, email, a sheet) without touching the bot.

## Troubleshooting

| Symptom | Check |
|---|---|
| Bot silent on spam | `/diag` privacy line. `/setprivacy` Disable in BotFather, then remove and re-add the bot to the group. |
| "I am missing the 'Ban users' right" in a report, or a red rights line in `/diag` | Give the bot Delete messages, Ban users, Invite users via link. |
| Captcha never restricts | Group is in shadow mode. `/mode enforce`. |
| Claude failed in `/diag` | Key missing or wrong, or outbound HTTPS blocked. Look at `docker compose logs zyroguard`. |
| n8n failed in `/diag` | Network name mismatch, or workflow not active. Test with `docker exec zyroguard python -c "import urllib.request;print(urllib.request.urlopen('http://n8n:5678/healthz').status)"`. |
| Bot left the group | Added by someone without access, or by a user who is not an admin there. Add it from the `OWNER_IDS` account, give that person access, or put the id in `ALLOWED_CHAT_IDS` and `docker compose up -d`. |

A cron job on the host runs `/opt/zyroguard/backup.sh` daily at 03:15 (server time). It makes a consistent
copy with SQLite's backup API into `/opt/zyroguard/backup/zyroguard-<date>.db` and keeps 14 days.
The script lives in this repo as `backup.sh`; install it with:

```bash
scp backup.sh root@YOUR_SERVER:/opt/zyroguard/backup.sh && ssh root@YOUR_SERVER "sed -i 's/\r$//' /opt/zyroguard/backup.sh && chmod 700 /opt/zyroguard/backup.sh && (crontab -l 2>/dev/null | grep -q zyroguard || (crontab -l 2>/dev/null; echo '15 3 * * * /opt/zyroguard/backup.sh >> /opt/zyroguard/backup/backup.log 2>&1') | crontab -)"
```

Copy backups off the box now and then (they hold message excerpts):

```bash
scp root@YOUR_SERVER:/opt/zyroguard/backup/zyroguard-$(Get-Date -Format yyyy-MM-dd).db .
```

## Restore

```bash
ssh root@YOUR_SERVER "cd /opt/zyroguard && docker compose stop && docker run --rm -v zyroguard_zyroguard-data:/data -v /opt/zyroguard/backup:/b alpine sh -c 'cp /b/zyroguard-YYYY-MM-DD.db /data/zyroguard.db && rm -f /data/zyroguard.db-wal /data/zyroguard.db-shm' && docker compose start"
```

`.env` is not in any backup on purpose. Keep the bot token, the API key and the two secrets in a
password manager so a rebuilt server can be set up again.

## Rollback

Before a rebuild, tag the running image so a bad build can be undone in seconds:

```bash
ssh root@YOUR_SERVER "docker tag zyroguard-zyroguard:latest zyroguard:prev 2>/dev/null; cd /opt/zyroguard && docker compose up -d --build"
```

To roll back:

```bash
ssh root@YOUR_SERVER "cd /opt/zyroguard && docker tag zyroguard:prev zyroguard-zyroguard:latest && docker compose up -d --no-build"
```
