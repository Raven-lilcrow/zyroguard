# ZyroGuard

A self-hosted Telegram bot for any kind of community (shops, brands, creators, gaming, education,
local groups, software products, crypto projects), in two parts:

- **ZyroGuard** keeps the group safe: spam and phishing protection, group cleaning (captcha,
  newcomer rules, flood control), teaching by example, and admin tools with Why, Undo and Teach as
  spam on every report.
- **ZyroMod** (optional, `/zyromod`; "support" in the code and settings) answers members' questions
  from the group's own files and hands everything else to the group's team.

Step-by-step guides for group admins are at the top of DOCUMENTATION.md: "How do I set up ZyroGuard
in my Telegram group?" and "How do I set up ZyroMod in my Telegram group?".

Python (aiogram 3) in a Docker container beside n8n on a VPS. Long polling, so no public port and
no webhook.

ZyroGuard, in order, for every message from a non-admin:

1. Structural hard rules: inline buttons, forwards from channels, `via_bot`, invite links,
   links and media from new members, bot mentions from new members, impersonation names,
   flood, the same text posted by several accounts.
2. Regex rules from `zyroguard/rules.yaml` plus per-group custom rules (`/rule add`).
   Unicode lookalikes and zero-width characters are folded before matching.
3. Soft scoring. Anything in the gray zone goes to the Claude judge, which returns strict
   JSON. Act only above the confidence threshold. API errors never delete anything.
   Fake job / task offers and account phishing (login-code and OTP requests included) always
   go to the judge, never removed by rules alone; fake jobs are delete-only until the same
   sender's 5th. Per group: `job_rules`, `phishing_rules`, `job_strike_after`.
4. Action: delete, then the strike ladder (mute 24h, ban 30d, ban forever by default).
   Every action is written to the audit table, reported to the mod log with Undo / Why
   buttons, and mirrored to n8n when `N8N_EVENT_URL` is set (it is not on the live server).

Join gate: CAS lookup, link-in-name check, emoji captcha (pick the word for a picture),
restricted for 24h after verifying. Service messages are deleted.

ZyroMod (`/zyromod`, `zyroguard/support.py` + `zyroguard/kb.py`): an admin uploads .txt, .md or
.pdf files in a private chat (10 MB per group), sets a support email, an optional contact link
and optional instructions for the bot (tone, do's and don'ts, links to include). Questions asked
with `/ask`, by @mention or as a reply to the bot are then answered only from those files; anything
they do not cover gets the group's can't-answer message. A bare `/ask` asks back for the question.
Files are read in memory, cut into sections and searched with the same on-server meaning model as
the spam check. See DOCUMENTATION.md section 12b.

## One-time setup

1. **BotFather**: `/newbot`, copy the token into `.env` as `BOT_TOKEN`.
   Then `/setprivacy` -> Disable, so the bot sees every group message.
2. **Your id**: DM the bot `/id`, put the number in `OWNER_IDS`.
3. **Add the bot to the group** and promote it to admin with these rights:
   Delete messages, Ban users (this is "restrict"), Invite users via link.
4. **Group id**: run `/id` inside the group, put it in `ALLOWED_CHAT_IDS`, or simply add the
   bot to the group as the owner: it remembers groups added by owners and permit holders and
   leaves any group added by anyone else.
5. **Mod log**: create a private group, add the bot, run `/id` there, set `MODLOG_CHAT_ID`
   (global default) or `/set modlog_chat_id <id>` per group.
6. `docker compose up -d --build`, then in the group run `/diag`. Every line should be a check mark.
7. Stay in **shadow** mode for a few days (`/mode shadow`, the default). The mod log shows
   what the bot *would* do. Flip with `/mode enforce` when you trust it.

## Commands

Admins of the group (checked live against Telegram, cached 5 min), in the group or in a
private chat with the bot (`/use` picks the group). Most accept a reply, `@username`, or a
numeric id. Durations: `30m`, `4h`, `3d`, `2w`, `1d12h`, `0` or `forever`.

| Group | Commands |
|---|---|
| Moderation | `/ban [dur]`, `/mute [dur]`, `/kick`, `/warn`, `/unban`, `/unmute`, `/strikes`, `/pardon`, `/report` |
| Lists | `/allow`, `/disallow`, `/allowed`, `/allowdomain`, `/blockdomain`, `/rule add <regex> [action] [dur]`, `/rule del <id>`, `/rule list` |
| Visibility | `/stats [dur]`, `/log [n]`, `/undo <id>`, `/why <id>` |
| Teaching | `/spam` (reply, or `/spam <id>` for an action, or the Teach as spam button on a report), `/notspam`, `/examples`, `/forget <n>` |
| Settings | `/settings` (menu in DM), `/settings list`, `/set <key> <value>`, `/unset <key>`, `/mode shadow|enforce`, `/pause <dur>`, `/resume`, `/setstrike`, `/setstriketime`, `/setwarnlimit`, `/setwarnmode`, `/setflood`, `/setfloodmode`, `/setnewaccount`, `/setcaptchatime`, `/setcaptchafail`, `/setrestrict`, `/setjudgemode`, `/setcasmode`, `/setsafety`, `/setsafetymode`, `/setgreeting` |
| Questions | `/ask <question>` (or @mention / reply to the bot), `/clear`, `/contact` (private chat) |
| ZyroMod | `/zyromod` (private chat: files, email, contact link, instructions, test, unanswered questions, on/off) |
| Troubleshooting | `/diag`, `/test <text>`, `/id`, `/help [topic or command]`, `/use` (private chat: pick the group) |
| Owner only | `/groups`, `/usage [dur]`, `/permit`, `/revoke`, `/permits`, `/purge`, `/reloadrules`, `/addadmin`, `/removeadmin`, `/addmod`, `/removemod`, `/showadmin` |

`/help` in DM gives the layered guide. `/help time` explains the time syntax.

A command with the bot tagged first (`@zyroguard_bot /spam`) runs as that command. Telegram only
parses commands at the very start of a message, so an outer middleware
(`assistant.MentionedCommands`, registered in `main.py`) strips the leading mention and shifts the
entities before any router sees the message.

## Settings keys

`/settings list` prints them with current values. Defaults live in `zyroguard/settings.py`.
Every action-type setting accepts `delete`, `warn`, `kick`, `mute [dur]`, `ban [dur]`, `none`.

## Rules file

`zyroguard/rules.yaml` is mounted read-only into the container. Edit it on the server and
restart the container. Only asking for a seed phrase acts immediately; soft rules
add weight, and a score at or above `thresholds.judge` asks Claude, at or above
`thresholds.hard` acts without Claude.

## Claude judge

`ANTHROPIC_API_KEY` in `.env`. Set `ANTHROPIC_BASE_URL` to route through an Anthropic-compatible
gateway; the SDK picks it up automatically. Model `JUDGE_MODEL` (default `claude-opus-5`) with
`JUDGE_EFFORT` (default `low`; the live server runs `high`, and `ASSISTANT_EFFORT=medium` for
questions), 256 output tokens, cached system prompt. Per-group daily cap
(`judge_daily_cap`, default 500). Leave the key empty to run rules-only.

## Internal HTTP

`GET /health` and `GET /stats?since=24h&chat_id=...` on port 8080, reachable only on the
docker network. The daily summary is sent by the bot itself. Never publish this port.
`/stats` lists every group, so it needs the `X-ZyroGuard-Secret` header with the value of
`N8N_EVENT_SECRET`, and it is off when that is empty. `/health` shows no group data.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python -m pytest
```

443 tests (2026-10-14), all against a fake Telegram, no network.

**End-to-end check of a new group** (`tools/e2e_newgroup.py`): builds the same dispatcher and
routers as `main.py`, fakes Telegram, and walks a brand-new group through being added, `/mode`,
`@zyroguard_bot /diag`, the captcha, five moderation cases (normal chat, crypto pitch, fake job,
phishing, an OTP warning), support setup, every way of asking (`/ask`, @mention, reply, bare `/ask`,
the reminder, the cooldown), `@zyroguard_bot /spam` as a reply, and the group's instructions.
Locally it checks the wiring with fake AI. On Windows keep `PYTHONIOENCODING=utf-8`: without it the
first emoji the bot prints fails inside a handler and the run waits forever.

```bash
E2E_LOCAL=1 CAS_ENABLED=false PYTHONIOENCODING=utf-8 .venv/Scripts/python tools/e2e_newgroup.py
```

On the server it runs in a throwaway copy of the live image with the real rules, model and Claude
(temp database, nothing sent anywhere, about 15 AI calls). Copy the file to `/tmp/zge2e/` first, then:

```bash
docker run --rm --memory=1g --env-file /opt/zyroguard/.env -e PYTHONPATH=/app -v /tmp/zge2e:/w:ro zyroguard-zyroguard:latest python /w/e2e_newgroup.py
```

Last run 2026-10-14 on the live image: 31 of 31 checks passed.

**End-to-end check of a full client setup** (`tools/e2e_full_setup.py`): a new group set up the way a
client does it, with the real files in `client-kit/test-group/` (a made-up bike shop: a markdown FAQ,
a .txt, a 2-page PDF, and a file with a password that must be refused). It sets a log chat, solves a
captcha, checks moderation in shadow and enforce mode and presses Why, Teach as spam and Undo on the
reports, runs every `/zyromod` step in the private chat (files sent as Telegram documents, wrong
email, short link, instructions, Test a question, Turn on), lets members ask in every way, then uses
the dashboard (Unanswered, own can't-answer message, Remove a file). It uses the shared fake
Telegram in `tools/fake_telegram.py`, which records every call and lets a test press inline buttons
(the new-group check still has its own copy).

```bash
E2E_LOCAL=1 CAS_ENABLED=false PYTHONIOENCODING=utf-8 .venv/Scripts/python tools/e2e_full_setup.py
```

On the server, put `e2e_full_setup.py`, `fake_telegram.py` and the test files (in `files/`) in
`/tmp/zge2e/`, then:

```bash
docker run --rm --memory=1g --env-file /opt/zyroguard/.env -e PYTHONPATH=/app -e E2E_FILES=/w/files -v /tmp/zge2e:/w:ro zyroguard-zyroguard:latest python /w/e2e_full_setup.py
```

Last run 2026-10-14 on the live image: 46 of 47 passed; the one miss was the test's own question
("do you rent tandem bikes?" was correctly answered "no" from the rental list instead of getting the
can't-answer message), since changed. About 25 AI calls.

Copy `.env.example` to `.env`, fill it in, then:

```bash
.venv/Scripts/python -m zyroguard.main
```

The "/" command menus are registered by the bot itself at startup (see `zyroguard/menu.py`), no BotFather step needed.

## License

Apache License 2.0. See `LICENSE`.
