# ZyroGuard Documentation

@zyroguard_bot is a Telegram bot for any community: brands and online shops, creators, gaming,
education, local groups, software products and crypto projects. It runs 24/7 on its own server and
does two jobs. This document is organized around them:

- **ZyroGuard** keeps the group safe. It removes spam, scams and phishing before members see them,
  stops fake accounts at the door, keeps the chat clean, and gives admins tools with an Undo for
  every action.
- **ZyroMod** answers members' questions from the group's own files (FAQ, product guides, policies)
  and sends anything the files don't cover to the group's own team. It is optional and stays off
  until an admin sets it up with `/zyromod`. In the bot's menus and settings it is also called
  support mode (`support`, `support_email` and so on).

Start here:

- [How do I set up ZyroGuard in my Telegram group?](#how-do-i-set-up-zyroguard-in-my-telegram-group)
- [How do I set up ZyroMod in my Telegram group?](#how-do-i-set-up-zyromod-in-my-telegram-group)
- [More how-to questions](#more-how-to-questions)

After the guides, sections 1 to 11 and 13 cover ZyroGuard in detail, sections 12 and 12b cover
ZyroMod, and sections 14 to 16 cover contact, using ZyroGuard in your own group, and security.
To run your own copy of the bot on a server see `deploy.md`; for the code see `README.md`.

## ZyroGuard features: keeping the group safe

`/start` and `/help` open with the same introduction and a button for each of these four.

| Feature | What it does | Key commands | Details |
|---|---|---|---|
| **Spam and phishing protection** | Removes scam links, investment pitches, fake jobs and task scams, account phishing and fake support accounts. Rules catch the clear cases, an AI judge checks the unclear ones. Starts in shadow mode (report only). | `/mode enforce`, `/test`, `/rule`, `/blockdomain`, `/allow` | Sections 1, 5 |
| **Group cleaning** | Captcha for new members, 24 hours without links or forwards for newcomers (their pictures go through the safety check), join/leave messages removed, flood control, the safety check for explicit or abusive content, notices that delete themselves. | `/setcaptchatime`, `/setrestrict`, `/setflood`, `/setsafety` | Sections 1 (join gate), 6, 13 |
| **Teaching by example** | Admins teach spam with `/spam` (as a reply, with an action number, or the Teach as spam button on a report). The meaning match then spots messages that mean the same thing in about 50 languages. Starts in a trial until `/set similarity on`. | `/spam`, `/notspam`, `/examples`, `/forget` | Section 5, "Teaching by example" |
| **Admin tools** | Ban, mute, kick, warn and undo, plus a report with Undo, Why and Teach as spam buttons for every action, and `/log`, `/why`, `/stats`. All of it works from the private chat too. | `/ban`, `/mute`, `/warn`, `/undo`, `/log` | Sections 4, 7 |

## ZyroMod features: answering members

| Feature | What it does | Key commands | Details |
|---|---|---|---|
| **Answers from your files** | Members' questions are answered only from the files the group's admins send (.txt, .md or .pdf, 10 MB per group), in the member's own language. Nothing is invented, and links that are not in the files are removed. | `/zyromod`, `/ask` | Section 12b |
| **Every way of asking** | `/ask` with the question, tagging @zyroguard_bot, or replying to one of its answers (a follow-up). A bare `/ask` asks back for the question. A plain question that matches the files gets a short reminder. | `/ask`, `/clear` | Section 12b, "In the group" |
| **Hand-off to your team** | A question the files don't cover gets the group's own can't-answer message, support email and an optional button (ticket page, Discord, website). | Setup steps 2 and 3 | Section 12b |
| **Your own instructions** | Tone, do's and don'ts, links to always include, topics to decline. Per group, and they can't make the bot answer outside the files. | Setup step 4 | Section 12b, "How the instructions are used" |
| **Closing the loop** | A list of the questions it couldn't answer in the last 7 days, so the files get better every week, plus answered and unanswered counts in the daily summary. | Unanswered button | Section 12b |
| **The general assistant** | With ZyroMod off, the bot still answers questions about the group and about using ZyroGuard. | `/ask`, @mention | Section 12 |

---

## How do I set up ZyroGuard in my Telegram group?

About 10 minutes, then a few days of watching. You need to be the group's creator, or an admin who
can add admins.

1. **Get access.** Open @zyroguard_bot, tap **Start**, send `/contact` and say which group it is
   for. Without access the bot leaves any group it is added to, and you must be an admin of the
   group you add it to (section 15).
2. **Add the bot as an admin.** Open your group's info, tap **Administrators** (on some apps
   **Edit**, then **Administrators**), then **Add Admin**, and search for `@zyroguard_bot`. Turn on
   **Delete messages**, **Ban users** and **Invite users via link**, leave the rest off, and save.
   If your app asks you to add it as a member first, add it, then promote it.
3. **Check the welcome.** In the group the bot says "ZyroGuard is here. All rights look good.
   Admins: I start in report-only mode." You also get a private message from the bot with
   **Run check** and **Open settings** buttons. If the bot says it still needs rights, go back to
   step 2.
4. **Run the check.** Send `/diag` in the group, or tap **Run check**. Every line should be a check
   mark. A cross comes with a one-line fix.
5. **Choose where reports go.** You get every report in your private chat with the bot. To use a
   separate log group instead: create a private group, add @zyroguard_bot to it, send `/id` there, then send
   `/set modlog_chat_id <that id>` to the bot.
6. **Tell it about your group (optional, recommended).**
   - `/set judge_context an online shop community, product links and prices are normal`, so the AI
     judge knows what normal talk looks like here.
   - `/set protected_names YourBrand`, so accounts copying your name with lookalike letters are
     removed when they join.
   - `/settings` opens a button menu for everything else: captcha type and time, newcomer rules,
     languages, strikes.
7. **Watch for a few days.** The group starts in shadow mode: the bot reports what it would do and
   touches nothing. Try `/test` followed by any message to see what it would do with it.
8. **Turn it on.** When the reports look right, send `/mode enforce`. From then on it removes spam,
   applies the strike ladder (mute for a day, then a 30-day ban, then a permanent ban) and posts a
   short notice in the group that deletes itself.
9. **Day to day.** Every report has **Why**, **Undo** and **Teach as spam** buttons. `/log` shows
   the last actions and `/stats` what was caught. Teach a scam it missed by replying `/spam` to it,
   and fix a mistake with **Undo** or `/notspam`.

Every command except the ones that need a reply also works from your private chat with the bot.
With more than one group, `/use` picks which group the commands are about.

## How do I set up ZyroMod in my Telegram group?

About 10 minutes. ZyroGuard must already be in the group (steps 1 to 4 above). Use the group's
creator account, or an admin who can add admins: only they can set where members are sent for help.
Have your files ready: .txt, .md or .pdf, up to 10 MB in total. Only use what you would put on a
public help page, because members can be shown anything in them.

1. **Open the setup.** Send `/zyromod` in the group and tap **Set up support privately**, then
   **Start**. Or open your private chat with @zyroguard_bot and send `/zyromod`. If you run several
   groups, tap the one to set up.
2. **Step 1 of 4: your files.** Send the files one by one. Each one shows "Added" with its name.
   Save Word or Google Docs files as PDF first. Files with passwords or keys in them are refused.
   Tap **Done** when all of them are in. A file sent later with the same name replaces the old one.
3. **Step 2 of 4: support email (required).** Send the email members should use when the files
   don't have the answer, like `support@yourshop.com`.
4. **Step 3 of 4: contact button (optional).** Send a full link for a button under the can't-answer
   message (a ticket page, Discord, your website, a Telegram chat), or tap **Skip**. Short links such
   as bit.ly are refused.
5. **Step 4 of 4: instructions (optional).** Tap the example to copy it, edit it and send it back,
   or tap **Skip**. The example:

   ```
   Tone: friendly and natural. Simple words, short and organized answers.
   Do: if a question is unclear, ask what they mean instead of guessing. End answers with our guide for more details: https://yourcompany.com/help
   Don't: mention other products or competitors. If someone brings up politics or uses offensive words, say you can only help with questions about our product.
   ```

6. **Test it.** Tap **Test a question** and ask what a member would ask. Members don't see tests.
   Send `/cancel` when you're done.
7. **Turn it on.** Tap **Turn on**.
8. **Tell your members how to ask:** `/ask` followed by the question, tagging @zyroguard_bot, or
   replying to one of its answers. A plain question that matches your files gets a short reminder.
   Ten seconds between questions is enough; a second question within 8 seconds gets no reply.
9. **Every week.** Send `/zyromod` and tap **Unanswered** to see what members asked that your files
   don't cover. Add those answers to your files and send them again with the same name.

Change anything later from the same `/zyromod` menu: **Add files**, **Remove a file**, **Change
email**, **Contact button**, **Can't-answer message**, **Preview**, **Instructions for me**, **Test a
question**, **Unanswered**, **Turn off**, **/ask reminders**, **Remove all files**.

## More how-to questions

| Question | Answer |
|---|---|
| How do I switch from report-only to acting? | `/mode enforce`. Back with `/mode shadow`. |
| How do I see what the bot would do with a message? | `/test` followed by the text, or reply `/test` to a message. Nothing happens to anyone. |
| How do I undo a mistake? | Tap **Undo** on the report, or send `/undo` with the action number. It lifts the mute or ban, removes the strike and teaches the bot that the message was fine. |
| How do I teach it a scam it missed? | Reply `/spam` to the message (`@zyroguard_bot /spam` works too). For one it already removed, tap **Teach as spam** on the report or send `/spam` with the action number. |
| How do I trust someone, like a partner who posts links? | Reply `/allow` to them, or send `/allow @name`. |
| How do I pause the bot for a while? | `/pause 2h`, then `/resume`. |
| How do I add or update ZyroMod files later? | `/zyromod`, **Add files**, send the file. The same name replaces the old copy. |
| How do I see what members asked that the files don't cover? | `/zyromod`, **Unanswered** (last 7 days). |
| How do I change the support email, button or message? | `/zyromod`, then **Change email**, **Contact button** or **Can't-answer message**. Every change is posted to your reports. |
| How do I turn ZyroMod off? | `/zyromod`, **Turn off**. Files and settings stay. |
| How do I remove the bot from a group? | Remove it from the group like any member. |

---

## 1. How it works

```
Telegram  <-- long polling -->  ZyroGuard (Python)
                                   |  rules        instant, free
                                   |  Claude judge only for gray-zone messages
                                   |  actions      delete, mute, ban, kick
                                   |  SQLite       strikes, settings, audit log
                                   +--> mod-log group and n8n
```

For every message from a non-admin, the bot runs these checks in order. The first hit wins.

1. **Skip list.** Admins, allowlisted users, and the bot itself are never checked.
2. **Structural hard rules.** Things only spam bots do: inline buttons on a message, forwards
   from channels, messages sent "via bot", invite links, links from new members (and their media when
   `media_unverified` is `delete`),
   bot mentions of other bots from new members, impersonation names, flooding, and the same
   text with a link or contact posted by several accounts. Most of the newcomer rules only
   delete the message (no strike), so a mistake costs nothing.
3. **Regex rules.** Asking someone for a seed phrase or private key is the only wording that
   acts on its own, plus any custom rules admins add with `/rule add`. Unicode lookalikes and
   invisible characters are folded first so `USDТ` with a Cyrillic T still matches.
4. **Soft scoring.** Everything else is a score: commission and get-rich pitches (USDT
   schemes included), guaranteed returns, fake jobs, fake support, wallet addresses, company
   boilerplate, "DM me to earn", money words, urgency, trading signals, adult menus, emoji arrows
   and long pitches. A score of 10 or more acts on its own. A score of 4 to 9 goes to the AI judge.
   Normal talk in any kind of group ("is the sale still on?", "did anyone get the airdrop?",
   "never share your password") scores too low to matter.
5. **AI judge.** Gray-zone messages go to Claude (model and effort set in `.env`) which returns a
   strict spam / not spam verdict with a confidence. The bot acts only above the confidence
   threshold. If the API fails, nothing is deleted.
6. **Action.** Delete the message, then apply the strike ladder. Every action is written to
   the audit log and posted to the mod-log group with Undo and Why buttons. If `N8N_EVENT_URL` is
   set, n8n gets a copy too.

Edited messages are re-checked, because spammers post clean text then edit a link in.

### Shadow vs enforce

Every group starts in **shadow** mode. The bot logs what it *would* do to the mod log but
touches nothing. Run a few days like this, check the log for mistakes, then switch with
`/mode enforce`. Switch back any time with `/mode shadow`.

### The strike ladder

Each spam hit adds a strike. Strikes expire after 30 days by default. Shadow mode never
records strikes, so switching to enforce always starts everyone at zero. Several messages
in one burst (within 90 seconds of the last strike) count as one offence, so a spammer who
pastes the same pitch three times is muted once, not banned.

| Strike | Default action |
|---|---|
| 1 | mute 24h |
| 2 | ban 30d |
| 3 and later | ban forever |

Timed mutes and bans are lifted automatically when they expire and the lift is posted to
the mod log. All three steps are configurable per group (section 6).

### Fake jobs and task scams

"Like and follow TikTok videos, earn 200-800 USD daily, message me", "rate products for
commission on every task", "deposit to unlock VIP tasks", a WhatsApp recruiter with a salary
per day, in English, Tagalog/Taglish (₱, GCash), Spanish and the other rule languages. The
soft rules `job_bait`, `task_scam`, `easy_money` and `pay_rate`, next to a contact line, add up
quickly. Two things are different from other scams:

- **The AI judge always decides.** However many job words a message has, it is never removed by
  the rules alone, so a member sharing a real job opening is not punished by wording. (Admins
  are exempt anyway.) Only when the judge is off or not set up do the rules act alone.
- **Gentle handling.** A fake job or task offer (by the rules, or the judge's `job_scam`
  category) is only deleted: no strike, no mute. From the same person's 5th one in that group
  the normal strike ladder applies (`/set job_strike_after 5`; 1 = strike from the first,
  0 = never). Undone actions do not count. A job pitch mixed with a crypto pitch (USDT
  commission, wallet addresses) is handled as a normal scam. `/test` shows which applies.

`/set job_rules off` turns all of this off for one group: it then keeps exactly the rules and
handling it had before the job rules were added (the rules marked `only: jobs` in rules.yaml are
skipped and the old `only: no_jobs` versions used).

### Account phishing

"Your account will be deleted in 24 hours, verify it here: telegram-verify-center.com", fake
Telegram / Meta / GCash support, a page "violating community standards", and above all anyone
asking for a **login code, OTP or two-step password**, which is how Telegram accounts get
stolen. The soft rules `code_request` (5), `account_threat`, `verify_link`, `brand_domain` (a
made-up domain pairing a brand with a security word), `phish_deadline`, `kyc_selfie`,
`support_impersonation` and Tagalog `tl_account_threat` add up to the judge or beyond.

Members warn each other by quoting these messages, so a phishing-only hit **always goes to the
AI judge**, never removed by the rules alone. The judge clears a quoted warning; when it is 95%
or more sure it is phishing, `impersonation_action` applies (ban by default, no strike ladder).
Only if the judge is off or not set up do the rules act alone. "Never share your OTP" and
"the app sends an OTP" do not count as a request.

`/set phishing_rules off` turns this off for one group (it keeps the rules it had before, the
new ones are marked `only: phishing`).

### Join gate

When someone joins:

1. **CAS check.** The user id is looked up in the Combot Anti-Spam database. Listed
   spammers are banned before they can type.
2. **Name scan.** A display name containing a link blocks the join. Words alone ("crypto",
   "support") do not. The bio scan is off by default.
3. **Captcha.** The newcomer is muted and asked to pick the word that matches a picture
   (emoji mode, the default; math mode asks a small sum, button mode is a single tap). No
   answer within the timeout, or three wrong taps, means kick. Only that user can press it.
4. **Restriction window.** For 24 hours after verifying, and until they have sent 5 messages,
   links, forwards and bot mentions from that member are removed. Their pictures go through the
   safety check instead (`/set media_unverified delete` removes all their media).
5. **Cleanup.** "X joined" and "X left" service messages are deleted.

By default everyone gets the captcha on every join, even after leaving and coming back
(`captcha_every_join`). Turn it off to ask only the first time.

---

## 2. Who can do what

| Role | How it is decided | What they can do |
|---|---|---|
| Member | anyone else | `/ask`, `/clear`, `/help`, `/id`, `/report`; admin commands are ignored |
| Trusted member | an admin ran `/allow` on them | exempt from every filter in that group, no commands |
| Group admin | live Telegram admin of that group, cached 5 min | all admin commands for that group, exempt from filters |
| User | got access and added the bot to their own group | full control of that group, from their private chat with the bot (section 15) |

A demoted admin loses control within five minutes. Anonymous admins (posting as the group)
are accepted.

Any command also works with the bot tagged in front of it: `@zyroguard_bot /spam` (as a reply)
does exactly what `/spam` does. Telegram only counts a command at the very start of a message,
so until 2026-10-14 the tagged form was taken as a question for ZyroMod instead. A tag
for another bot, a command in the middle of a sentence or a path like `/usr/bin` is left alone.

---

## 3. Time and action syntax

Every command that takes a duration uses the same format:

| Write | Means |
|---|---|
| `30s`, `30m`, `4h`, `3d`, `2w` | seconds, minutes, hours, days, weeks |
| `1d12h` | combinable |
| `0` or `forever` | permanent |

Telegram treats anything under 30 seconds or over 366 days as permanent, and the bot tells
you when that happens.

Every setting that takes an action accepts one of:

`delete`, `warn`, `kick`, `mute [duration]`, `ban [duration]`, `none`.

`mute` or `ban` without a duration is permanent.

---

## 4. Moderation commands

Use them in the group. Target a user by replying to their message, by `@username`, or by
numeric id.

| Command | What it does |
|---|---|
| `/ban [dur]` | Ban. `/ban @user 7d` for a timed ban, no duration = forever. |
| `/mute [dur]` | Mute. `/mute @user 2h`. |
| `/kick` | Remove from the group. They can rejoin. |
| `/warn` | Add a strike. Used as a reply it also deletes that message. At the warn limit the warn action fires. |
| `/unban` | Lift a ban. |
| `/unmute` | Lift a mute. |
| `/strikes` (or `/warns`) | Show a user's active strikes. |
| `/pardon` (or `/resetwarns`) | Clear a user's strikes. |
| `/report` | Reply to a message to send it to the mod log for a human to look at. |

---

## 5. Lists and custom rules

| Command | What it does |
|---|---|
| `/allow @user` (or `/approve`) | Exempt a user from every filter in this group. |
| `/disallow @user` (or `/unapprove`) | Remove the exemption. |
| `/allowed` (or `/approved`) | List exempt users. |
| `/allowdomain example.com` | Links to this domain are always fine. |
| `/blockdomain example.com` | Links to this domain are always spam. |
| `/rule add <regex> [action] [dur]` | Add a custom pattern. `/rule add join my channel ban 7d`. Without an action it uses the strike ladder. Patterns are limited to 200 characters and may not use nested repeats, two repeats in a row that match the same characters (like `\s*\s*`), or backreferences, which could stall the bot. Custom rules check the first 1,000 characters of a message. A rule that still takes too long is switched off and the mod log says so. |
| `/rule del <id>` | Remove a custom rule. |
| `/rule list` | Show custom rules with their ids. |

Custom rules are checked before the built-in ones and can carry their own action.

### Teaching by example (meaning match)

Regex rules only catch the exact wording. The meaning match compares every message (15
characters or more) with example spam by what it means, in about 50 languages, so a reworded
scam or the same scam in Korean or Thai still lands near the example. It runs on the server
with a small model (paraphrase-multilingual-MiniLM-L12-v2, about 220 MB, one CPU thread);
no message text leaves the server.

| Command | What it does |
|---|---|
| `/spam` (as a reply) | Saves the message as a spam example for this group and removes it. Does not punish the sender, add `/ban` for that. Type it in the reply yourself: picking it from the command menu sends it without the reply, and then the bot explains both ways instead. |
| `/spam <action number>` | For a message the bot already removed: learns from the text saved with that action (number from `/log` or the report). Works in the group and in the private chat. On a shadow-mode catch it also removes the message. Undoing that action later switches the example off again. |
| **Teach as spam** button | On every report of a rule or AI judge catch that has text. One tap does the same as `/spam <action number>`; the button then disappears and the report says who taught it. |
| `/notspam` (as a reply, or with an action number) | Saves the message as a normal example, so similar messages count less. |
| `/examples` | Status, counts, and the last 10 examples with their numbers, including learned ones still waiting. |
| `/forget <number>` | Stops using one example, or stops a waiting one before it counts. |
| `/undo` | Also saves the undone message as a normal example, switches off a spam example with the very same text, and drops anything learned from that action. |

How a match counts: very close to known spam (0.80 or more) adds 4 to the score, which is
enough for the AI judge to look at it; close (0.70 or more) adds 2, which only matters
together with other signs. Very close to a known-normal message (0.85, and closer than to any
spam) takes 2 off. A match never acts on its own. The judge is told how close the match was.

The library starts with a seed set: common scams in English, Russian, Chinese, Indonesian and
Vietnamese, plus the text of past judge calls and high-scoring rule hits. Seed and `/spam all`
examples are shared by every group; `/spam` examples belong to the group they were taught in.

The `similarity` setting decides what happens: `shadow` (default) records each close match
with what the verdict would have been, without changing anything; `on` counts it; `off` skips
it. Leave it in shadow for a few days, check the record, then `/set similarity on`. Until then
taught examples are saved but don't change any decision, and the bot's reply to `/spam` says so. The full
review is `tools/similarity_report.py` (read-only, run it inside the container the same way as
`tools/health_check.py`): every match that would have changed a verdict, what happened to that
sender anyway, and which examples do the matching.

**Learning from sure catches** (`similarity_learn`, off by default until the meaning-match trial is reviewed). When the bot acts in
enforce mode on a message the AI judge was 95% or more sure about, or on a strong rule hit
(the score passes the hard threshold without the meaning match's own points, or a seed-phrase
request or 🔞 marker), the message becomes a spam example for that group. It waits 24 hours
first and only starts counting if nobody undid the action; `/undo` drops it at any time, and
`/forget` stops it while it waits. Name checks, house rules and language deletes are never
learned. At most 20 a day per group, and a text that is already known or already waiting is
not stored twice. Works in `shadow` as well as `on` (the library grows while you review), not
in shadow *mode* for the group (nothing was really acted on there).

**Examples for the AI judge** (`judge_examples`, off by default until the meaning-match trial is reviewed). When a message goes to the
judge, it also sees up to 3 labeled examples closest in meaning (0.60 or closer), spam or
normal, as precedent. That is how a `/notspam` or `/undo` correction reaches the judge: a
message very close to one the admins marked normal is judged with that in view. The example
numbers are kept with the action, so you can see what the judge was shown. Example text is
fenced as untrusted, like the message itself.

---

## 6. Settings

Two ways to change them:

- `/settings` in the group sends a button menu to your DM. Press a button to flip a value.
- `/set <key> <value>` in the group changes any key. `/unset <key>` restores the default.
  `/settings list` prints every key with its current value.

Shortcut commands exist for the common ones and are listed with each key.

### General

| Key | Default | What it does |
|---|---|---|
| `mode` | `shadow` | `shadow` logs only, `enforce` acts. Shortcut `/mode shadow` or `/mode enforce`. |
| `modlog_chat_id` | server default | Chat that receives action reports for this group. |
| `group_notice` | `on` | In enforce mode, post a short line in the group when spam is removed (who, what, reason, undo hint). Shadow mode never posts in the group. |
| `notice_ttl` | `10m` | The bot's own group messages (that notice, moderation command replies, the greeting) are deleted after this. `0` keeps them. Deletion survives restarts, it is queued in the database. |
| `digest_time` | `09:00` | Time of the daily summary, Asia/Manila. |

Also: `/pause <dur>` makes the bot watch but take no action for a while, useful during an
event. `/resume` ends the pause early.

### Join gate

| Key | Default | What it does |
|---|---|---|
| `captcha` | `on` | Gate new members behind a verify button. |
| `captcha_mode` | `emoji` | `emoji` = pick the word for a picture, `math` = answer a simple sum, `button` = just tap. |
| `captcha_time` | `2m` | Time allowed to pass. Shortcut `/setcaptchatime 5m`. |
| `captcha_fail` | `kick` | What happens on timeout. Shortcut `/setcaptchafail ban 1d`. |
| `captcha_every_join` | `on` | Everyone gets the captcha each time they join, even if they passed it before and left. `off` = only the first time. |
| `strict_unverified` | `on` | Delete everything an unverified newcomer sends. |
| `skip_verified_anywhere` | `off` | Trust users who passed the captcha in any group this bot serves. |
| `restrict_window` | `24h` | Newcomer rules (no links or forwards) last this long after verifying. `0` disables. Shortcut `/setrestrict 72h`. |
| `new_member_msgs` | `5` | A member also counts as new until they have sent this many messages. |
| `cas` | `on` | Check joiners against Combot Anti-Spam. |
| `cas_action` | `ban` | Action for CAS-listed users. Shortcut `/setcasmode kick`. |
| `bio_scan` | `off` | Treat a join with a link or a get-rich pitch in the bio as a hard hit. Off by default because many real people have a link in their bio. |
| `clean_service` | `on` | Delete "joined" and "left" messages. |
| `new_account_age` | `1h` | Accounts the bot first saw less than this long ago get the harsher action on any hard hit. Shortcut `/setnewaccount 1h ban 30d` sets both. |
| `new_account_action` | `ban 30d` | That harsher action. |
| `greeting` | empty | Optional text sent after a member verifies. Shortcut `/setgreeting Welcome!`. |

### Filters

| Key | Default | What it does |
|---|---|---|
| `link_policy` | `new` | `all` = allow links, `new` = block links from new members, `allowlist` = only allowlisted domains for everyone. |
| `media_unverified` | `allow` | `allow` = images from new members are judged by content like everyone else's; `delete` = remove all media from new members for their first day. |
| `forward_captcha` | `on` | Anyone (except admins and trusted users) who forwards a message gets a quick captcha as a reply. Solved: the forward stays. Not solved in time or 3 wrong taps: the forward is removed and they get a strike, so the strike ladder decides (by default mute 1 day, then ban 30 days, then ban). Enforce mode only. |
| `forward_captcha_time` | `2m` | How long the forwarder has to solve it. |
| `languages` | empty | Languages people may write in, e.g. `en` or `en,tl` (names work too: `/set languages English, Tagalog`). Empty or `all` = every language. A message in another language is just deleted: no strike, no mute. Short or mixed messages ("ok", "gm", Taglish with plenty of English) count as unknown and are never deleted for their language. A scam in any language is still punished as spam. |
| `language_scope` | `new` | Who the languages rule applies to: `new` = new members only, `all` = everyone except admins and trusted users. |
| `protected_names` | empty | Names only admins may use, comma separated, e.g. `/set protected_names YourBrand`. Anyone else whose display name or username copies one with lookalike letters (a small L in place of a capital I, a 1 or 0 in place of a letter, a Cyrillic Р, small caps) is removed when they join and on any message, with `impersonation_action`. Using the real spelling ("YourBrand fan") is allowed, it only adds a little weight to a message that also looks like a pitch. "ZyroGuard" is protected in every group. |
| `similarity` | `shadow` | The meaning match (see "Teaching by example"): `shadow` records close matches without counting them, `on` adds them to the score, `off` skips it. |
| `similarity_learn` | `off` | Sure catches become spam examples for the group after a 24-hour wait, unless undone. |
| `judge_examples` | `off` | The AI judge also sees up to 3 labeled examples closest in meaning. |
| `impersonation_action` | `ban` | Used for lookalike names, for fake "the contract address is moving / old tokens will be burned" notices that pass the hard score, and for messages the AI judge is at least 95% sure are impersonation, phishing or a wallet scam. Skips the strike ladder: fake admins do not get a warning mute first. |
| `flood_count` | `5` | Messages inside the window that count as flooding. Shortcut `/setflood 5 10s` sets both. |
| `flood_window` | `10s` | The window. |
| `flood_action` | `mute 1h` | Action on flooding. Shortcut `/setfloodmode kick`. |
| `repeat_count` | `3` | Identical text from this many different users within 10 minutes is a spam wave. |
| `job_rules` | `on` | Find fake job and task offers. `off` = the group keeps the rules it had before they were added. |
| `phishing_rules` | `on` | Find account phishing and login-code / OTP requests; they always go to the AI judge. `off` = the group keeps the rules it had before. |
| `job_strike_after` | `5` | Fake job and task offers are only deleted until the same person's Nth one here, then the strike ladder applies. 1 = from the first, 0 = never. |

### Claude judge

| Key | Default | What it does |
|---|---|---|
| `judge` | `on` | Send gray-zone messages to Claude. |
| `judge_context` | empty | "About this group": one line telling the judge what is normal here, e.g. `/set judge_context an online shop community, product links and prices are normal`. Stops normal shop or trading talk from reading like a pitch. |
| `judge_threshold` | `0.85` | Minimum confidence to act. Lower catches more, risks more mistakes. |
| `judge_action` | `delete` | What to do when Claude says spam. `delete` adds a strike. Shortcut `/setjudgemode mute 12h`. |
| `judge_daily_cap` | `500` | Maximum Claude calls per day for this group. |

**About the daily cap.** Most spam is caught by the rules for free. Only gray-zone messages
go to Claude, and each one is a paid API call. The cap limits how many of those calls the
bot makes per group per day, resetting at midnight. When the cap is hit the bot keeps
working on rules alone until the next day. It exists so a spam wave or someone deliberately
posting borderline text cannot run up the bill. 500 is far more than a normal group uses.
Raise it with `/set judge_daily_cap 2000` only if `/stats` shows the cap being reached.

The model and effort are set server-wide in `.env` (`JUDGE_MODEL`, `JUDGE_EFFORT`).
Current setting on the server: Opus 5, high effort, routed through an API gateway. Sonnet 5 at low effort is the cheaper option with the same request shape.

### Strikes

| Key | Default | What it does |
|---|---|---|
| `strike1` | `mute 24h` | First strike. Shortcut `/setstrike 1 mute 1h`. |
| `strike2` | `ban 30d` | Second strike. `/setstrike 2 ban 7d`. |
| `strike3` | `ban` | Third and later. `/setstrike 3 ban`. |
| `strike_ttl` | `30d` | Strikes older than this are forgotten. Shortcut `/setstriketime 90d`. |
| `warn_limit` | `3` | Manual `/warn` count that triggers the warn action. Shortcut `/setwarnlimit 2`. |
| `warn_action` | `mute 1d` | Action at the warn limit. Shortcut `/setwarnmode ban 1d`. |

Warnings and automatic strikes share the same counter.

---

## 7. Seeing what the bot did

| Command | What it does |
|---|---|
| `/stats [dur]` | Actions in the last 24h (or `7d`, `30d`): by action, by rule, judge calls, undos. |
| `/log [n]` | Last n actions with their numbers, plus Why / Undo buttons for the latest five. |
| `/undo <id>` | Reverse an action by its number (shown in every log-group post and in `/log`): restores the user and clears the strike. |
| `/why <id>` | Explain exactly which rule or judge verdict caused an action. Also works as a reply to a bot action. |

Every log-group post ends with its action number so `/why` and `/undo` always resolve.

**Reports.** Every action (or would-be action in shadow mode) is posted with the user, the
reason, the duration and expiry, and Undo / Why buttons. Reports go to the group's log chat
if one is set, otherwise to the private chat of the user who added the bot, otherwise to the
server-wide log group. The bot also posts a daily summary at `digest_time`. If
`N8N_EVENT_URL` is set, n8n receives a copy of every event on the `zyroguard-event` webhook.

---

## 8. Troubleshooting commands

| Command | What it does |
|---|---|
| `/diag` | In the group. Checks privacy mode, admin rights, admin cache, Claude, n8n, database, uptime. Each line is a check mark or a cross with a one-line fix. |
| `/test <text>` | Dry run. Shows the result, the reason, the AI judge's view and the action it would take, without touching the group. Also works as a reply to a message. |
| `/id` | Show the chat id and your user id. |
| `/help [topic or command]` | Layered help. `/help` (and `/start`) open the introduction with the four features; `/help settings` for a topic, `/help ban` for one command. Feature pages: protect, cleaning, teach, admin. Detail pages: start, punish, trust, rules, settings, time, check, safety, private, support. |

Common fixes:

| Symptom | Fix |
|---|---|
| Bot ignores spam | `/diag` privacy line. In BotFather `/setprivacy` Disable, then remove and re-add the bot to the group. |
| "I am missing the 'Ban users' right" in a report | Give the bot Delete messages, Ban users, Invite users via link. |
| Captcha shows but nobody is muted | Group is in shadow mode. `/mode enforce`. |
| Claude line red in `/diag` | Key or base URL wrong in `.env` on the server. |
| Bot left the group | It was added by someone without access, or by a user who is not an admin of that group. Get access first (section 15), then add it again as a group admin. |

---

## 9. Command aliases

Some commands answer to a second name too: `/approve`, `/unapprove`, `/approved` for
`/allow`, `/disallow`, `/allowed`, and `/warns`, `/resetwarns` for `/strikes`, `/pardon`.

---

## 10. Server settings

These live in `/opt/zyroguard/.env` on the server and need a container restart to change.

| Key | Meaning |
|---|---|
| `BOT_TOKEN` | From BotFather. Secret. |
| `ANTHROPIC_API_KEY` | Claude key, or the key for your API gateway. Secret. |
| `ANTHROPIC_BASE_URL` | Your gateway's URL, to route through an Anthropic-compatible gateway. Empty for Anthropic direct. |
| `JUDGE_MODEL`, `JUDGE_EFFORT` | `claude-opus-5`, `high`. |
| `OWNER_IDS` | The Telegram user id of the bot owner, the one person who runs the bot. |
| `ALLOWED_CHAT_IDS` | Groups the bot serves from the start. Groups you add later are remembered in the database. Anyone else adding the bot makes it leave. |
| `MODLOG_CHAT_ID` | Default mod-log chat for groups that have not set their own. |
| `MODE` | Default mode for new groups, `shadow`. |
| `CAS_ENABLED` | Server-wide switch for CAS lookups. |
| `SIMILARITY` | Server-wide switch for the meaning match, `on` by default. Set `off` to skip loading the model (saves about 300 MB of memory). |
| `N8N_EVENT_URL`, `N8N_EVENT_SECRET` | Where to mirror events, and the header secret both sides check. Without the secret the internal `/stats` endpoint is off. |

Restart after editing:

```bash
ssh root@YOUR_SERVER "cd /opt/zyroguard && docker compose up -d"
```

Logs:

```bash
ssh root@YOUR_SERVER "cd /opt/zyroguard && docker compose logs --tail=100"
```

---

## 11. First-day checklist

1. From your main account: open @zyroguard_bot, Start, `/id`. That number goes in `OWNER_IDS`.
2. Add the bot to the group, make it admin with Delete messages, Ban users, Invite users via link.
3. `/id` in the group for the group id. Make a private mod-log group, add the bot, `/id` there.
4. Put the three ids in `.env`, restart.
5. `/diag` in the group. Every line green.
6. `/test We are Royal Dynasty International looking for USDT holders, 10% commission` should
   show matched rules and "would delete + mute".
7. Watch the mod log in shadow mode for a few days.
8. `/mode enforce`.

## 12. ZyroMod basics: asking the bot questions

Members can ask the bot things and get a short plain-text answer from Claude (same model as the judge, effort set by `ASSISTANT_EFFORT`, default medium).

How to ask:

| Where | How |
|---|---|
| In a group | `@zyroguard_bot` followed by the question, or reply to one of the bot's messages, or `/ask <question>` |
| Reply to someone else's message with `/ask` | The bot explains that message or answers it |
| Private chat with the bot | Just write. Only admins and members of guarded groups get answers |

What it does: explains things, gives step-by-step guides, helps with ZyroGuard's own commands (the help topics are part of its instructions), answers in the language of the question.

In a group with ZyroMod on (section 12b), answers come only from that group's own files instead.

What it does not do, on purpose:

- No web access, no live data (news, prices, weather, whether a site is up). It says so in one sentence and gives what general knowledge it can.
- It never takes moderation actions. If asked to ban or mute, it tells you the command an admin can use.
- It does not see the rest of the chat, only the message that triggered it (plus the message it was a reply to).

Guard rails:

| Setting | Default | Meaning |
|---|---|---|
| `assistant` | `on` | Turn question answering on or off for this group (`/set assistant off`). |
| `assistant_daily_cap` | `200` | Maximum answers per day in this group. When reached the bot says so. |

Also built in: 8 seconds cooldown per user (a second question within 8 seconds of the first is skipped without a reply, with ZyroMod on too), questions in a group are only answered after the message passed the spam checks, and answers are capped at about 250 words.

### Follow-up questions and /clear

The bot remembers the last 4 exchanges between one person and itself in one chat, for 30 minutes after the last question, so follow-ups like "and how do I undo that?" work. Older or idle history is dropped on its own, which keeps every call small.

| Command | Who | What it does |
|---|---|---|
| `/clear` | anyone | Forgets your own earlier questions in this chat. The next question starts fresh. |
| `/clear all` | admins | Forgets everyone's questions in this group. |

`/clear` only touches remembered questions. Bans, mutes, strikes, warnings, rules and settings live in the database and are never affected.

## 12b. ZyroMod: answers from your own files (`/zyromod`)

A group can give ZyroGuard its own FAQ and product files. Members then ask with `/ask` and get
answers taken only from those files. When the files do not have the answer, the member gets the
group's can't-answer message with the support email and an optional contact button. Every group
has its own files, email and link; one client never sees another's.

**Setup, in a private chat with the bot.** An admin sends `/zyromod` (in the group it replies
with a button that opens the private chat, so members never see the files). The first time it
walks through four steps:

1. Files: `.txt`, `.md` or `.pdf`, as many as fit in 10 MB for the group. A file with the same
   name replaces the old one. Word or Google Docs: save as PDF first.
2. Support email (required). Shown whenever the bot cannot answer.
3. Contact link (optional): any link (ticket page, Discord, website, a Telegram chat), shown as a
   button with your own button text.
4. Instructions for me (optional, Skip button): how the bot should answer in this group, up to
   1500 characters. The step shows a tap-to-copy example in the same shape:
   "Tone: friendly and natural. Simple words, short and organized answers. / Do: if a question is
   unclear, ask what they mean instead of guessing. End answers with our guide for more details:
   https://yourcompany.com/help / Don't: mention other products or competitors. If someone brings
   up politics or uses offensive words, say you can only help with questions about our product."

Then a preview of the can't-answer message, a test box ("Test a question", members do not see
it, and it names the closest matching file and section), and **Turn on**. After that,
`/zyromod` opens a menu: add or remove files, change or remove the email and button, write your
own can't-answer message, preview it, set or remove the instructions, test, see the questions it
could not answer, turn the `/ask` reminders on or off, turn support on or off, remove all files.
The menu lists each file with its size (and pages for a PDF) and ends with the next thing to do,
like "Next: set the support email."

**How the instructions are used.** They go to the AI with every answer in that group only (in
the cached part of the prompt, so they add almost nothing to the cost). The bot follows them for
tone, for what to add (a docs link at the end of answers, a support hours line) and for how to
handle kinds of questions (politics, insults, competitors). Facts and links written in them count
like the files, so a link in the instructions is not removed from answers. They cannot change the
core rules: answers come only from the files and the instructions, a question neither covers gets
the can't-answer message, nothing is invented, and the instructions are never revealed. With
instructions set, every question addressed to the bot goes to the AI, even ones far from the
files, so rules like "say you can't help with politics" apply (about 2 US cents each); without
them, clearly unrelated questions get the can't-answer message with no AI call. Saving refuses
passwords and keys, short links and sites on the group's block list. Tested 2026-10-13 with the
real model: with "point members to our documentation" the install answer ended with the docs
link, a competitor comparison and an insult got a one-line refusal, "who should I vote for?" got
"I can't help with that.", and a weather question still got the can't-answer message.

**In the group.**

| Member does | Bot does |
|---|---|
| `/ask <question>` | Searches the group's files, answers from them in the member's language. Not covered: the can't-answer message ("I don't have the answer to that one. Email the team at ... or tap the button below."). Partly covered: the part it found, plus "For the rest, email the team at ...". |
| Admin replies `/ask` to a member's question | Answers that question from the files. |
| `@mentions` the bot or replies to it (also `@bot /ask ...`) | Answered from the files, exactly like `/ask`. A reply to one of its answers is a follow-up and uses the earlier question. |
| `/ask` alone (what tapping /ask in the command menu sends) | "What's your question? Reply to this message and I'll answer it.", with Telegram's reply box opened for them. Their reply is answered. |
| Asks a plain question that matches the files, without `/ask` or tagging the bot | A short reminder, auto-deleted after `notice_ttl`: "Hi Ana! If you want an answer from me, start your question with /ask. Tap to copy:" with their own question ready to copy. At most once a day per person, one per minute per group. Admins never get it. |

While ZyroMod is on, answers come only from the files: no general knowledge about the
product, prices, dates or links. Links in an answer that are not in the files or the contacts are
removed before it is sent.

**How answers look.** The bot answers like someone on the group's team: it starts with the answer,
never mentions "the documents" or "the files", and stops when the answer is complete, usually in
1 to 3 sentences. Steps are numbered, three or more items become a bulleted list, commands and
codes are shown as tap-to-copy code, and at most one key fact (an amount, a deadline) is bold. A
clear yes or no that follows from the files counts as an answer ("Do you ship to Japan?"
"No, we only ship within the Philippines."). Greetings and thanks get one short line, and
attempts to change its instructions get "I can only help with questions about this group's
product or service." As a safety net the bot also trims filler openers and closers ("Great
question!", "Based on the documents", "Hope this helps!"), em dashes and arrows before sending.

**Reading the files.** Files from any tool are cleaned before anything else, so every group gets
tidy answers without preparing its files. Markdown from Notion, Google Docs, GitBook or GitHub loses
its formatting marks, front matter and GitBook tags; tables become "Column: value" rows; links keep
their address; code blocks stay copyable and comments inside them are never taken for titles. PDFs
lose their page numbers, words split across lines are joined again, and short title lines start a
new section. Running headers and footers are removed from PDFs of 4 pages or more (a line repeated
at the top or bottom of most pages); shorter PDFs keep them, which only adds a harmless extra line
to a section (seen in the 2-page test PDF on 2026-10-14). Each file remembers which reader version cut it
(`kb.READER_VERSION`, now 2): after a reader update the menu marks older files "send it again", and
sending the same file again is accepted instead of refused as a duplicate.

**How it finds the answer.** Each file is cut into small sections (headings and FAQ questions
start a new one; a section title keeps its last three heading levels). Each section gets a meaning vector from the same on-server model the spam check
uses, so nothing extra is loaded. A question is matched by meaning (works across languages and
rewording) and by keywords (exact names and numbers). Clearly unrelated questions get the
can't-answer message without any AI call. Otherwise Claude gets the matching sections (a small
FAQ, under about 16,000 characters, is sent whole and cached, so more questions within minutes cost a
tenth) and must answer only from them or reply that it cannot. Thresholds were calibrated on
2026-10-13 with `tools/support_calibrate.py`.

**Limits.** 10 MB of files per group and about 1.5 million characters of text (roughly 600 dense
pages), whichever comes first: reading stops there so a huge file cannot slow the server. Scanned
PDFs (pictures of pages) and password-protected PDFs are refused with a plain explanation. Files
containing an API key, private key, password line or recovery phrase are refused, because anything
in the files can be quoted to any member. Answers share `assistant_daily_cap` and the 8 second
cooldown. A big file takes a few minutes to read; the bot keeps checking spam meanwhile.

**Who may change what.**

| What | Who |
|---|---|
| Files, on/off, `/ask` reminders, test, unanswered list | Any admin of the group, and the user who added the bot |
| Support email, contact button link and text, can't-answer message, instructions | Only the user who added the bot, the group's creator and admins who may add new admins |

Every contact change (from `/zyromod` or `/set`) is posted to the group's reports with the old and
new value and who changed it. A contact link on the group's blocked-domain list, or a link
shortener, is refused. The email cannot be removed while ZyroMod is on.

**Storage and privacy.** The uploaded file is downloaded into memory, read, and dropped; it is
never written to disk. Only the extracted text, cut into sections, and their vectors are stored,
in the bot's database on the server, tagged with the group. Per question only the matching
sections (or the whole small FAQ) go to Claude, through the same route as the spam judge.
Removing a file deletes its text and vectors at once.
Daily database backups keep a copy for 14 days. The original file also stays in the admin's private
chat with the bot on Telegram until they delete it there.

**Settings** (`/set` works too, with the same permission checks):

| Key | Default | What it does |
|---|---|---|
| `support` | `off` | Answer `/ask` from the files. Needs at least one file and a support email. |
| `support_hint` | `on` | The reminder to use `/ask` for questions asked without it. |
| `support_email` | empty | Shown when the bot can't answer. Required while support is on. |
| `support_link` | empty | Optional contact button link. Any `https` link, no short links. |
| `support_link_label` | `Contact the team` | Text on that button, up to 40 characters. |
| `support_text` | empty | Your own can't-answer message, up to 500 characters. Empty = the default. The email is added under it, unless the message already contains it. |
| `support_instructions` | empty | Instructions for the bot's answers in this group (tone, do's and don'ts, links or details to include), up to 1500 characters. Easiest to set in `/zyromod`, Instructions for me. |

The daily summary and `/stats` show "Support questions: N answered, M I couldn't answer. Details in /zyromod".

## 13. Safety check: explicit and abusive content

Spam is not the only thing the bot removes. The safety check catches content that does not belong in a normal group:

- explicit photos or stickers (nudity, porn)
- gore and threats of violence against a person
- bullying, telling someone to hurt themselves
- slurs and hate against a group of people
- scam pitches carried by an image (fake jobs, giveaways, investment offers, wallet QR codes)

How it works:

1. Every photo, sticker or video from a normal member is shown to Claude, which also reads any text written on the image, in any language.
2. A text message is only checked when it contains a harsh word or a threat (the `safety_triggers` list in `rules.yaml`), so everyday chat costs nothing.
3. If Claude is at least 85% sure (`judge_threshold`), the message is deleted and the sender gets the `safety_action`, muted for 1 day by default. A scam on an image is treated as spam instead: `judge_action` and the strike ladder.
4. The log group gets a post with the reason and an Undo button. `/why` shows the category and how sure the check was.

Admins and trusted users are never checked. Casual swearing that is not aimed at anyone is left alone. If Claude refuses to describe an image at all, that image is treated as unsafe.

| Setting | Default | Meaning |
|---|---|---|
| `safety` | `on` | Safety check on or off. Shortcut `/setsafety on` or `/setsafety off`. |
| `safety_action` | `mute 1d` | What happens to the sender. The message is always deleted. Shortcut `/setsafetymode ban 7d`. |

Safety calls count toward the same `judge_daily_cap` as the spam judge.

## 14. Contact the admin

Anyone who meets the bot can reach the admin. A "✉️ Contact the admin" button appears on the `/start` message, when the bot leaves a group it does not guard, and when a stranger tries to ask it questions. `/contact` in a private chat does the same.

Flow: tap the button, the bot asks for the message, the person writes it in one go. The bot then:

1. sends it on Telegram right away to the account in `OWNER_IDS`, with a link to reply, and
2. emails it to `CONTACT_EMAIL` through n8n, if `CONTACT_WEBHOOK_URL` is set.

Limits: one message per person per 10 minutes, 2000 characters, `/cancel` to stop. Nothing about the person is stored.

Server settings in `.env`:

| Key | Meaning |
|---|---|
| `CONTACT_EMAIL` | Shown to people and used as the email recipient, e.g. raven@publicai.io |
| `CONTACT_WEBHOOK_URL` | n8n webhook that sends the email, e.g. `http://n8n:5678/webhook/zyroguard-contact-<random>` |
| `CONTACT_WEBHOOK_SECRET` | Shared secret the n8n workflow checks before sending |

n8n side: import `n8n/ZyroGuard-Contact.json`, set the secret in the "Secret ok?" node to the same value, pick your Gmail credential in the Gmail node, and switch the workflow on. Without the webhook, Telegram delivery still works and the reply to the person mentions the email address.

---

## 15. Using ZyroGuard in your own group

A user is the person who adds ZyroGuard to their group. Each user's groups are fully separate
from everyone else's.

**Getting access.** Open @zyroguard_bot, tap **Start**, send `/contact` and say which group it is
for. Once you have access, add the bot to that group as an admin. You must be an admin of the group
yourself. Without access the bot leaves the group and shows the "Contact the admin" button. When
the bot joins, you get a private message from it with **Run check** and **Open settings** buttons.

**Managing from the private chat.** Every admin command works in your private chat with the bot:
`/settings`, `/diag`, `/mode`, `/log`, `/stats`, `/rule`, `/set`, and the rest. With one group it
is picked automatically. With more, `/use` shows buttons to choose which group the commands apply
to. Commands that need a reply (like `/ban` as a reply) still happen in the group. Telegram admins
of your group can use the commands inside the group as usual.

**Reports.** Actions in your group are reported to your private chat with the bot, with the same
undo numbers. You can point them at a log group instead with `/set modlog_chat_id`. Reports from
your group never go to anyone else's log.

**Separation.** Every table is keyed by group id: settings, strikes, trusted users, domains,
custom rules, audit rows, captcha state, AI usage. You can only control groups registered to you
or where you are a Telegram admin. `/why`, `/undo` and the settings buttons check the same rule.

**AI budget.** Each user has a daily cap on AI calls (spam judge, safety check and questions
together) across all of their groups, 1500 by default. Past the cap the rules still work, only the
AI parts pause until the next day.

**When access ends.** The bot leaves your groups and tells you in your private chat. Send
`/contact` to renew.

---

## 15b. Evasion handling

Text is folded before matching: NFKC normalisation, zero-width and direction-control characters
removed, Cyrillic/Greek lookalikes and small-caps letters mapped to Latin, and spaced-out
letters ("U S D T") squeezed back together. Hidden link targets in formatted text are
extracted. Edits that add a link are scored higher. Forwards from newcomers are removed
(no strike). Judge outages are reported once an hour per group, not once per message.

## 16. Security notes

- Buttons only work on the message they belong to. A callback replayed against another
  message of the bot (for example a captcha prompt in a different group) is rejected.
- `/set modlog_chat_id` only accepts your own private chat or a chat where you are an admin
  and the bot is present.
- Custom rule patterns are checked for length and for regex shapes that can hang the matcher.
- Error rows never contain the bot token: download errors are redacted before they are stored.
- A user can only register groups where they are an admin. The bot leaves channels.
- Private-chat questions are capped at 30 per person per day for members. Group admins and users
  have no cap. Only people who have taken part in a guarded group
  can chat with the bot privately; `/test` in private needs a group you control.
- Edited messages go through the spam rules and the safety check again.
- Posts made "as a channel" that hit a hard rule are deleted and the channel is banned from
  posting in the group.
- The safety check has its own daily cap (`safety_daily_cap`, default 300) so pictures cannot
  starve the spam judge, and a picture already judged clean in a group is not checked again.

