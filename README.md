# ZyroGuard

ZyroGuard is a self-hosted Telegram bot that keeps community groups clear of spam, scams and
phishing, and can also answer members' questions from the group's own files. It runs as a single
Docker container on a small server. To get started you need a bot token from @BotFather and your
own Telegram user id. A Claude API key is optional and switches on the AI parts.

It was first deployed in a 52,000-member crypto community, but nothing in it is crypto-only: it is
meant for shops and brands, creators, gaming, courses, local groups and software products alike.
In its first 23 days of enforcing in that community it took 403 automatic actions, admins undid
none of them, and the AI was needed 20 times, for about nine cents in total. The
[case study](case-study/CASE-STUDY.md) has the full story.

## What it does for your group

**ZyroGuard keeps your group safe.**

- New members solve a quick picture puzzle before they can talk, and known spammers are blocked
  the moment they join.
- Scams, fake job offers, "earn $300 a day" pitches, fake support accounts and attempts to steal
  accounts are removed before your members see them, in many languages.
- Explicit, violent, hateful or harassing pictures and messages are removed.
- Accounts that copy your group's name with lookalike letters are removed.
- Your admins and trusted members are never touched, and the bot only acts when it is sure.
- It starts in report-only mode, so you can check its choices for a few days before turning it on.
- Every action comes with a report and three buttons: **Why**, **Undo** and **Teach as spam**.

**ZyroMod answers your members (optional).**

- Give it your FAQ, product guide or policies, and it answers members' questions from those files
  only, in their own language. It never makes things up.
- When your files don't have the answer, it sends members to your support email or help page.
- It keeps a list of the questions it couldn't answer, so you know what to add.

Everything is managed inside Telegram. There is no website to log into.

## How it works

ZyroGuard reads every message in the groups it guards and decides in layers, cheapest first. Clear
cases are settled by rules on the spot. Only the unclear ones go to Claude, and even then the AI's
verdict passes through the same strike ladder, admin guard and Undo button as everything else. A
new group starts in shadow mode, where the bot only reports what it would have done.

The bot has two parts.

### ZyroGuard: keeping the group safe

- **Join gate.** Every newcomer is looked up in the Combot Anti-Spam (CAS) list, a display name
  with a link in it is refused, and everyone else solves a short captcha (pick the word that
  matches a picture). Until they have been in the group 24 hours and sent a few messages, they
  count as new: their links are removed and their pictures go through the safety check.
- **Structural checks.** Things ordinary members rarely do: inline buttons, forwards from channels,
  messages sent through another bot, invite links, flooding, and the same text posted by several
  accounts within minutes.
- **Pattern rules.** About 75 rules in [`zyroguard/rules.yaml`](zyroguard/rules.yaml), in English
  plus Russian, Chinese, Vietnamese, Tagalog, Indonesian, Spanish and Arabic. Lookalike letters,
  Cyrillic swaps and invisible characters are folded back before matching. Admins can add their
  own patterns per group.
- **Scoring.** Only a couple of rules act on their own, such as a request for a seed phrase.
  Every other rule adds points: below 4 nothing happens, 4 to 9 goes to the AI judge, and 10 or
  more acts straight away.
- **AI judge.** Claude sees the unclear message together with what the rules found (is this a new
  member, which language, how close to known spam) and returns a verdict with a confidence. The
  bot acts only when it is at least 85% sure. If the API is down, nothing is removed.
- **Meaning match.** A small multilingual model running on the server compares each message with
  known spam by meaning, so a reworded or translated scam still lands close. A match only adds
  points, it never acts alone, and each group starts it in a trial mode.
- **Safety check.** Explicit, violent, harassing or hateful pictures and messages are checked by
  Claude and removed.
- **Fake jobs and account phishing.** Members often quote these to warn each other, so they always
  go to the AI judge instead of being removed for their wording. A fake job offer is only deleted
  at first; the strike ladder starts from the same person's fifth.
- **Lookalike names.** A group can protect its own name. If it protects `AcmeAI`, anyone who joins
  or posts as `AcmeAl` or `Acm3AI` is removed.
- **Strike ladder.** A 24-hour mute, then a 30-day ban, then a permanent ban, all adjustable.
  Strikes expire after 30 days, and a burst of messages counts as one offence.
- **Reports with Why, Undo and Teach as spam.** Every action is logged and reported to the admins
  with buttons to see the reason, reverse it, or teach the bot that the message was spam.
- **A guard at the very end.** Admins and trusted members are skipped early and checked again right
  before any ban or mute, so a bug further up can never act on them.

### ZyroMod: answering members (optional)

- **Answers from your files.** An admin sends .txt, .md or .pdf files to the bot in a private chat,
  up to 10 MB per group. Members get answers taken only from those files, in their own language.
- **Several ways to ask.** `/ask` with the question, tagging the bot, or replying to one of its
  answers.
- **Hand-off to your team.** A question the files don't cover gets the group's own "can't answer"
  message, with a support email and an optional button to a ticket page, Discord or website.
- **Your own instructions.** Tone, do's and don'ts and links to include. They shape the answer but
  never widen what the bot knows.
- **Closing the loop.** A list of the questions it couldn't answer in the last 7 days shows what to
  add to the files.

Everything is run from Telegram: commands in the group, or a private chat with the bot that has a
button menu for every setting. There is no web dashboard.

## Requirements

- A Linux server with Docker and Docker Compose. Give the container 1 CPU and about 1 GB of
  memory; the meaning model uses most of it.
- A Telegram bot token.
- Optional: an Anthropic API key, or an Anthropic-compatible gateway. Without it ZyroGuard runs on
  its rules, the meaning match and the captcha. The AI judge, the safety check and question
  answering stay off.

## Installation

```bash
git clone https://github.com/Raven-lilcrow/zyroguard.git
```

```bash
cd zyroguard && cp .env.example .env && chmod 600 .env
```

Fill in `.env` (see [Environment variables](#environment-variables)). The included
`docker-compose.yml` joins an existing n8n network called `n8n_default`. If you don't run n8n,
use the [standalone compose file](#example-docker-composeyml) below instead.

```bash
docker compose up -d --build
```

```bash
docker compose logs -f zyroguard
```

The first build bakes the meaning model (about 220 MB) into the image, so the container never
downloads anything while it runs. When the log shows `ZyroGuard 0.1.0 starting as @yourbot`, set
up the bot in Telegram as described in the next section and send `/diag` in your group.

For updates, backups, restoring and rolling back on a live server, see [deploy.md](deploy.md).

## Setting up the Telegram bot

### Get a token

Open @BotFather in Telegram, send `/newbot` and pick a name and a username. Put the token it gives
you in `.env` as `BOT_TOKEN`. Treat it like a password: anyone who has it controls the bot.

### Turn off privacy mode

By default a bot only sees commands. Send `/setprivacy` to @BotFather, pick your bot and choose
**Disable**, so ZyroGuard can see every message. If the bot is already in a group, remove it and
add it again for the change to apply.

### Add your Telegram id

Start a private chat with your bot and send `/id`. Put the number in `OWNER_IDS` in `.env` and
restart the container.

### Add the bot to a group

Add the bot to the group as an admin with three rights: **Delete messages**, **Ban users** and
**Invite users via link**. Leave the rest off. The bot posts a welcome; then send `/diag` in the
group, and every line should show a check mark. A failed line comes with a one-line fix.

The bot only stays in groups added from that account, groups added by a user with access who is
an admin there, and groups listed in `ALLOWED_CHAT_IDS`. It leaves any other group. The `/` command menus are registered by
the bot itself, so there is nothing to set up for them in @BotFather.

## Configuration

ZyroGuard has two layers of configuration. Server-wide values (secrets, your Telegram id, the AI model) live
in `.env`. Everything else is a per-group setting stored in the database and changed from Telegram.

### Environment variables

| Variable | Default | What it does |
|---|---|---|
| `BOT_TOKEN` | required | The token from @BotFather. |
| `OWNER_IDS` | empty | Your Telegram user id, as the bot owner. One person. |
| `ANTHROPIC_API_KEY` | empty | Claude API key. Empty runs without the AI judge, safety check and question answering. |
| `ANTHROPIC_BASE_URL` | empty | An Anthropic-compatible gateway. Empty uses api.anthropic.com. |
| `JUDGE_MODEL` | `claude-opus-5` | Model for the judge, the safety check and answers. |
| `JUDGE_EFFORT` | `low` | Reasoning effort for spam checks: `low`, `medium`, `high`, `xhigh` or `max`. |
| `ASSISTANT_EFFORT` | `medium` | Reasoning effort for answering members' questions. |
| `MODE` | `shadow` | Starting mode for new groups: `shadow` reports only, `enforce` acts. |
| `ALLOWED_CHAT_IDS` | empty | Groups the bot always serves, whoever added it. |
| `MODLOG_CHAT_ID` | empty | A private chat that receives reports for groups without their own log chat. |
| `CAS_ENABLED` | `true` | Look up newcomers in the Combot Anti-Spam list. |
| `SIMILARITY` | `true` | Load the meaning model. `false` saves about 600 MB of memory but also turns off the meaning match and ZyroMod. |
| `CONTACT_EMAIL` | empty | Shown with the "Contact the admin" button. |
| `CONTACT_WEBHOOK_URL`, `CONTACT_WEBHOOK_SECRET` | empty | Optional webhook that also emails contact messages (see [n8n](#n8n-workflows-optional)). |
| `OWNER_LEDGER_URL`, `OWNER_LEDGER_SECRET` | empty | Optional private activity record: every group added or refused, access change, admin action and setting change (with old and new value), plus a daily summary, sent as Google Sheets API calls to a webhook such as an n8n workflow. Events are kept in the database and retried until the webhook takes them. |
| `N8N_EVENT_URL`, `N8N_EVENT_SECRET` | empty | Optional webhook that receives a copy of every action. The secret also unlocks `/stats`. |
| `LOG_LEVEL` | `INFO` | Log level. |
| `DB_PATH` | `/data/zyroguard.db` | The SQLite database. |
| `RULES_PATH` | `zyroguard/rules.yaml` | The rules file. |
| `HTTP_HOST`, `HTTP_PORT` | `0.0.0.0`, `8080` | The internal HTTP API inside the container. |

### Per-group settings

There are 62 settings in eight groups. Open the button menu with `/settings` (it arrives in your
private chat), or use `/set <key> <value>`, `/unset <key>` and `/settings list`. Settings that take
an action accept `delete`, `warn`, `kick`, `mute <time>`, `ban <time>` or `none`. Times look like
`30m`, `4h`, `3d`, `2w`, `1d12h`, or `0` and `forever`.

| Group | Covers | Defaults worth knowing |
|---|---|---|
| General (5) | Mode, notices in the group, where reports go, daily summary | Shadow mode; notices delete themselves after 10 minutes; summary at 09:00 |
| Join (16) | Captcha, newcomer limits, CAS, join messages, brand-new accounts | Emoji captcha with 2 minutes to answer, kick on failure, captcha on every join, 24 hours of newcomer limits |
| Filters (17) | Links, media, forwards, languages, protected names, meaning match, flood, spam waves, fake jobs, phishing | Links blocked for new members; captcha on forwards; 5 messages in 10 seconds is a flood (mute 1 hour); meaning match on trial |
| Judge (6) | The AI judge | On, acts at 85%, deletes, at most 500 checks a day |
| Strikes (6) | The strike ladder and `/warn` | Mute 24 hours, ban 30 days, ban; strikes expire after 30 days; 3 warnings mute for a day |
| Safety (3) | The safety check | On, at most 300 checks a day, mutes the sender for a day |
| Assistant (2) | Answering general questions | On, at most 200 questions a day |
| Support (7) | ZyroMod | Off until set up with `/zyromod` |

Every setting, with its meaning and default, is listed in section 6 of
[DOCUMENTATION.md](DOCUMENTATION.md).

### Data and backups

Everything lives in one SQLite database in the `zyroguard-data` volume: settings, strikes, the
audit log, learned examples and the text of ZyroMod files. [`backup.sh`](backup.sh) makes a
consistent copy with SQLite's backup API and keeps 14 days; [deploy.md](deploy.md) shows how to run
it daily with cron and how to restore. `.env` is never part of a backup, so keep the token and keys
in a password manager.

The container runs as a non-root user on a read-only filesystem with every Linux capability
dropped. It needs no open port: it talks to Telegram by long polling.

### The rules file

[`zyroguard/rules.yaml`](zyroguard/rules.yaml) is mounted into the container read-only, so rules can
change without a rebuild: edit the file on the server, then `docker compose restart`. It
holds the score thresholds (`judge: 4`, `hard: 10`), the few rules that act alone, the scored rules
with their weights, impersonation patterns, names protected in every group, join and bio patterns,
domains that are always allowed, and the words that trigger the safety check. Each group can add
its own patterns with `/rule add <regex> [action] [time]`. Patterns that could stall the bot are
refused, each rule gets 50 ms per message, and a rule that runs out of time is switched off with a
note in the mod log. `/test <text>` shows exactly which rules a message hits and what the bot would
do.

### The AI judge

- **What it sees.** Only messages that scored 4 to 9, every fake-job and phishing hit, and pictures
  for the safety check. A clean message never reaches it, so it cannot invent a problem out of
  normal chat.
- **What it returns.** A category and a confidence. The bot acts at `judge_threshold` (85% by
  default) with `judge_action`. When it is at least 95% sure a message is impersonation or a wallet
  scam, `impersonation_action` applies (a ban by default) and the warning mute is skipped.
- **Knowing the group.** `/set judge_context` takes one line about what is normal here, like "an
  online shop community, product links and prices are normal". With `judge_examples` on, the judge
  also sees up to three of the closest messages this group has taught it.
- **Limits.** At most `judge_daily_cap` checks per group per day (500), `safety_daily_cap` safety
  checks (300), and a shared daily cap across all of a user's groups (1,500).
- **Cost.** The system prompt is cached. In the 52,000-member group, 20 checks in 23 days cost
  about nine cents.
- **Failures.** If Claude can't be reached, nothing is removed.

### Meaning match

The model (`paraphrase-multilingual-MiniLM-L12-v2`, about 50 languages) runs on the server's CPU in
roughly 60 ms per message: no API key, no cost per message, and no message leaves the server. A
message this close to known spam adds points:

| Similarity | Effect |
|---|---|
| 0.80 or more | Adds 4, enough for the AI judge to look even with no other sign |
| 0.70 to 0.79 | Adds 2, which only matters next to other signs |
| 0.85 or more to a known-normal message (and closer than to any spam) | Takes 2 off |

Messages under 15 characters are skipped. Every group starts with `similarity` on `shadow`: the bot
records what the match would have changed without letting it count. `/set similarity on` makes it
count once the admins are happy with what it recorded.

### Teaching the bot

- **`/spam`** as a reply (or `@yourbot /spam`) removes the message and saves it as a spam example
  for this group. `/spam <number>` does the same for something already removed, and so does the
  **Teach as spam** button on a report.
- **`/notspam`** as a reply saves a known-normal example. Pressing **Undo** on a report does this
  automatically, so the bot does not repeat the mistake.
- **`/examples`** lists what the group has taught, and **`/forget <number>`** removes one.
- With **`similarity_learn`** on, messages the judge was at least 95% sure about, and strong rule
  hits, become examples by themselves after a 24-hour hold (so an Undo can still stop them), at
  most 20 a day per group.

### Newcomers and the captcha

The captcha comes in three styles (`captcha_mode`: `emoji`, `math` or `button`). Newcomers are muted
until they answer; only they can press their buttons, and no answer within `captcha_time` (2
minutes) means `captcha_fail` (kick). By default everyone gets it on every join, even after leaving
and coming back. In enforce mode, anyone who forwards a message (admins and trusted members aside)
has to solve a quick captcha too; an unsolved one removes the forward and adds a strike
(`forward_captcha`). A member counts as new for `restrict_window` (24 hours) and until they have
sent `new_member_msgs` (5) messages, so an old silent account cannot skip the newcomer rules.
Accounts that joined less than `new_account_age` (1 hour) ago get `new_account_action` (a 30-day
ban) on any clear hit. The bio scan is off by default, because many real people have links in their
bio.

### Fake jobs and account phishing

"Like videos and earn $300 a day, message me", task scams, and "your account will be deleted,
verify here" or anyone asking for a login code. These go to the AI judge every time, because
members quote them to warn each other. Fake job offers are only deleted until the same person's
`job_strike_after` (5th) one; account phishing the judge is sure about is handled like
impersonation. `job_rules` and `phishing_rules` turn each set off for a group.

### Strikes and warnings

Each spam hit adds a strike, and strikes expire after `strike_ttl` (30 days). Several messages in
one burst (within 90 seconds of the last strike) count as one offence, so a spammer who pastes the
same pitch three times is muted once, not banned. Shadow mode never records strikes. Timed mutes and
bans are lifted on time and the lift is reported. `/warn` is separate: `warn_limit` warnings (3)
trigger `warn_action` (a one-day mute).

### Reports, notices and summaries

Reports go to the group's `modlog_chat_id` if one is set, otherwise to the private chat of the
user who added the bot, otherwise to `MODLOG_CHAT_ID`. Each report says who, what and why, with **Why**, **Undo** and **Teach as spam** buttons. In enforce mode
the group also sees a short notice that deletes itself after `notice_ttl`. Every group gets a daily
summary at `digest_time`, in Asia/Manila time (set in `zyroguard/scheduler.py`). `/log`,
`/why <number>` and `/stats` show the same history on demand.

Members can reply `/report` to a message to forward it to the admins' report chat, once every 5
minutes.

### Logging

`docker compose logs zyroguard` shows the bot's log at `LOG_LEVEL`. The bot token is masked in
every error it stores, and the internal HTTP API keeps no access log.

## Start in shadow mode

Every group starts in shadow mode. The bot reports what it would have done, marked as shadow, and
touches nothing: no deletes, no mutes, no strikes. Leave a new group in shadow mode for a few days
and read the reports. If something normal for your group gets flagged, teach it with `/notspam` or
give the judge a line of context with `/set judge_context`. `/test <text>` tries any message
without affecting anyone. When the reports look right, send `/mode enforce`; `/mode shadow` goes
back at any time. Because shadow mode never records strikes, nobody starts enforce mode with a
strike from the trial.

## ZyroMod: answers from your own files

1. Send `/zyromod` in the group and tap the button, or send it to the bot in a private chat. With
   several groups, pick one.
2. Send the files one by one (.txt, .md or .pdf; save Word or Google Docs files as PDF first).
   A file with a password or key in it is refused. Tap **Done**.
3. Set the support email members are sent to when the files don't have the answer. This is
   required.
4. Optionally add a contact button (a full link; short links are refused) and instructions for the
   bot, up to 1,500 characters.
5. Tap **Test a question** to try it privately, then **Turn on**.

Files are read in memory and never stored as files; only their text is kept, per group. A small set
of files (up to about 16,000 characters) is sent whole with every question so nothing can be
missed; larger ones are searched with the same meaning model as the spam check. Any link in an
answer that isn't in the files or the contacts is removed before sending. An answered question
costs about 2 cents. Unless the group has given the bot instructions, a question with nothing
related in the files gets the can't-answer message without calling the AI at all. Only the user who added the bot, the
group's creator and admins who can add new admins may change where members are sent for help, and every change is reported.

The full guide, including the weekly **Unanswered** list, is in
[DOCUMENTATION.md](DOCUMENTATION.md#how-do-i-set-up-zyromod-in-my-telegram-group).

## Commands

Group admins can use every admin command, either in the group or from a private chat with the bot
(`/use` picks the group there). Admin status is checked live against Telegram. Most commands take a
reply, an `@username` or a numeric id. Tagging the bot first (`@yourbot /ban`) works too.

| For | Commands |
|---|---|
| Members | `/ask <question>`, `/clear`, `/report` (as a reply), `/help`, `/id`, `/contact` (private chat) |
| Moderation | `/ban [time]`, `/mute [time]`, `/kick`, `/warn`, `/unban`, `/unmute`, `/strikes`, `/pardon` |
| Team | `/showadmin`, `/addadmin`, `/removeadmin`, `/addmod`, `/removemod` (see below) |
| Lists | `/allow`, `/disallow`, `/allowed`, `/allowdomain`, `/blockdomain`, `/rule add <regex> [action] [time]`, `/rule del <id>`, `/rule list` |
| History | `/log [n]`, `/why <number>`, `/undo <number>`, `/stats [time]` |
| Teaching | `/spam`, `/notspam`, `/examples`, `/forget <number>` |
| Settings | `/settings`, `/settings list`, `/set <key> <value>`, `/unset <key>`, `/mode shadow\|enforce`, `/pause <time>`, `/resume`, plus shortcuts like `/setcaptchatime`, `/setflood` and `/setstrike` |
| ZyroMod | `/zyromod` (files, email, button, instructions, test, unanswered questions, on and off) |
| Checks | `/diag`, `/test <text>`, `/use` |

`/help` in a private chat opens a guide by topic, and `/help <command>` explains any command.

## Running it for several groups

One ZyroGuard instance can guard many groups, each with its own settings, rules, examples and
reports.

- **Groups you add** from the account in `OWNER_IDS` are guarded and remembered.
- **`ALLOWED_CHAT_IDS`** lists groups the bot always serves, whoever adds it.
- **Users** are the people who add the bot to their own group after getting access from you. A
  user must be an admin of that group, controls it from their private chat with the bot, gets its
  reports there and shares one daily AI cap across their groups. When access ends, the bot
  leaves their groups.
- **Group admins** of a guarded group automatically get the admin commands for it.
- **Bot admins** (`/addadmin`) get the admin commands for one group without being a Telegram
  admin there. Only the user who added the bot, the group's creator, or admins who can add new
  admins can add or remove them, and every change is posted to the group's reports.
- **Trusted members** (`/allow` or `/addmod`) are skipped by the filters but get no commands.
  `/showadmin` lists everyone with power in a group.
- **Contact the admin.** Anyone can tap the button or send `/contact` in a private chat to message
  you, and `CONTACT_WEBHOOK_URL` can email a copy.

## Internal HTTP API

The container serves two endpoints on port 8080, inside the Docker network only. Never publish this
port.

- `GET /health` reports the version, uptime, the time of the last update, whether the database is
  writable and whether the judge is set up. The Docker health check uses it. It shows no group data.
- `GET /stats?since=24h&chat_id=<id>` returns action counts, and without `chat_id` the list of
  groups. It needs the `X-ZyroGuard-Secret` header with the value of `N8N_EVENT_SECRET`, and it
  is off while that is empty.

## n8n workflows (optional)

The [`n8n/`](n8n) folder has two workflows for a self-hosted n8n on the same Docker network:

- `ZyroGuard-Modlog.json` receives a copy of every action (set `N8N_EVENT_URL` and
  `N8N_EVENT_SECRET`) and posts a daily digest built from `/stats`. Use it to send ZyroGuard's
  events anywhere n8n can reach, like Slack, email or a sheet.
- `ZyroGuard-Contact.json` emails the messages people send with the "Contact the admin" button
  (set `CONTACT_WEBHOOK_URL` and `CONTACT_WEBHOOK_SECRET`).

The bot already posts its own reports and summaries, so both are extras. Setup steps are in
[deploy.md](deploy.md#n8n-workflow).

## Example docker-compose.yml

For a server without n8n. It keeps the hardening of the included file and drops the n8n network.

```yaml
services:
  zyroguard:
    build: .
    container_name: zyroguard
    restart: unless-stopped
    env_file: .env
    environment:
      DB_PATH: /data/zyroguard.db
      RULES_PATH: /app/zyroguard/rules.yaml
    volumes:
      - zyroguard-data:/data
      - ./zyroguard/rules.yaml:/app/zyroguard/rules.yaml:ro
    read_only: true
    tmpfs:
      - /tmp
    mem_limit: 1g
    cpus: "1.0"
    pids_limit: 200
    stop_grace_period: 20s
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  zyroguard-data:
```

No `ports:` section is needed: the bot only makes outgoing connections, to Telegram, the CAS list
and the Claude API.

## Development

The commands below use the Windows layout (`.venv/Scripts/`). On Linux and macOS use `.venv/bin/`.

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
```

```bash
.venv/Scripts/python -m pytest
```

There are 476 tests, all against a fake Telegram, with no network access. An end-to-end check,
[`tools/e2e_newgroup.py`](tools/e2e_newgroup.py), drives the real dispatcher through a whole setup:
a brand-new group being added, the captcha, five moderation cases, ZyroMod setup and every way of
asking.

It runs locally with fake AI, or on a server in a throwaway copy of the image with the real model
and Claude. On Windows, set `PYTHONIOENCODING=utf-8`.

```bash
E2E_LOCAL=1 CAS_ENABLED=false PYTHONIOENCODING=utf-8 .venv/Scripts/python tools/e2e_newgroup.py
```

To run the bot outside Docker, copy `.env.example` to `.env`, fill it in, and start it:

```bash
.venv/Scripts/python -m zyroguard.main
```

## Further reading

- [DOCUMENTATION.md](DOCUMENTATION.md): the guide for group admins, with step-by-step setup for
  ZyroGuard and ZyroMod and every command and setting.
- [deploy.md](deploy.md): deploying, guarded updates, backups, restoring and rolling back.
- [case-study/CASE-STUDY.md](case-study/CASE-STUDY.md): how and why it was built, with real numbers.

## License

Apache License 2.0. See [LICENSE](LICENSE).
