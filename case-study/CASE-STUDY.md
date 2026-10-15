# ZyroGuard: a Telegram anti-spam bot where the AI is never the only thing that acts

## The problem

A 52,000-member crypto community on Telegram was getting the same scam every day: "we are looking for USDT holders, 10 to 25% commission per transaction, DM us". Fresh accounts would join, post once, and leave a trail of members who answered in private. The admins were deleting by hand, and the off-the-shelf bots either missed the scam because the wording kept changing or banned real members for saying "crypto".

The catch is the same as with any AI moderator. A bot that can ban people can also ban the wrong people, and in a group that size one bad afternoon of false bans costs more trust than a week of spam.

## What it is

ZyroGuard is a Python bot (aiogram, SQLite) running 24/7 in Docker on a small VPS, with Claude Opus 5 as a judge for borderline messages. New members solve an emoji captcha. Every message goes through a rules engine and, since October, a meaning match that compares it with known spam in any language. Known spammers are blocked against a public spam list. Forwards from outside channels need a captcha too. Admins get a mod log where every action has a Why button and an Undo button, and they run everything from Telegram commands, no dashboard. Since October it has a second part, ZyroMod, that answers members' questions from the group's own FAQ files.

The first deployment was a crypto community, but almost nothing in it is crypto-specific: the captcha, the scoring, the judge and the meaning match work the same for a shop, a creator's fans or a course's students. A handful of rules (seed-phrase requests, fake contract migrations) only add points when crypto scams show up.

## The design

**Rules act, the AI only breaks ties.** Rules come in two kinds. A few hard rules act on their own, like a request for a seed phrase. Everything else is soft and adds to a score. Below 4 nothing happens. At 10 or more the bot acts without asking anyone. Only the band in between goes to Claude, together with facts the rules gathered (is this a new member, how many messages have they sent, what language is it, how close is it to known spam). The judge never sees a clean message, so it cannot invent a problem out of normal chat, and its verdict goes through the same strike ladder as everything else: first strike a one-day mute, then a 30-day ban, then a permanent one. The one exception is earned: when the judge is at least 95% sure a message is impersonation or a wallet scam, the sender is banned straight away. Fake job offers and account phishing go the other way: members quote them to warn each other, so those never act on rules alone and always go to the judge.

**Shadow before enforce.** Every group starts in shadow mode: the bot logs what it would have done and does nothing. The admins read the log for a day, and only then switch to enforce. Shadow actions never add strikes, so a group's trial run cannot follow anyone into enforce mode. The same rule now applies to new features: the meaning match shipped in shadow and only records what it would have changed.

**A guard right before every action.** Admins, owners and allowlisted users are skipped early in the pipeline. They are also checked again at the very last step, inside the code that calls Telegram's ban and mute. If a bug anywhere upstream ever pointed an automatic action at an admin, that last check drops it. It has never fired in production, which is the point.

## The number

In 23 days of enforce mode on the 52,000-member group, ZyroGuard took **403 automatic actions and admins undid none of them**. Claude was needed only 20 times in that whole period, for about **nine cents** at list price.

The breakdown: 317 of the 448 accounts that joined never solved the captcha and were removed (71%), 35 known spammers were blocked on sight, and 51 of the 335 messages it screened were acted on (34 by rules, 17 by the judge). Of the 20 AI checks, 17 came back as spam, which says the rules are sending it the right messages. The pressure did not drop off: the first week had 113 actions, and the busiest day so far was day 21, with 28.

## Teaching it by meaning

Regex rules only know the wording they were written for. In October I added a meaning match: a small multilingual model (paraphrase-multilingual-MiniLM-L12-v2) turns every message into a vector, and the bot compares it with a library of known spam. Messages that mean the same thing land close together even when the words or the language are different.

It is built the same way as everything else in the bot. A close match only adds to the score: very close adds 4, which is enough for the judge to look, close adds 2, which only matters next to other signs. Being close to a known-normal message takes points off. A match never bans anyone by itself. Admins teach it by replying `/spam` or `/notspam` to a message, and every Undo automatically becomes a known-normal example, so the bot does not repeat the same mistake.

The model runs on the server's own CPU. No API key, no cost per message, and no group message leaves the box. Before switching it on I tested it with the real model against the live library, on messages that were not in it:

| | Result |
|---|---|
| The USDT scam reworded, and in Korean, Thai, Japanese, Turkish and Spanish | 0.76 to 0.89 similar; all reach the judge, four were "clean" to the rules before |
| Fake "verify your wallet" and "crypto recovery" pitches | 0.75 and 0.85 |
| 15 normal messages, including "I bought USDT on binance" and greetings in 4 languages | 0.29 to 0.57, none touched |
| Speed | about 60 ms per message |

The trade-off was memory. The model holds a vocabulary for 50 languages and takes about 600 MB, which lifted the bot from 200 MB to about 865 MB. I raised its cap to 1 GB and kept it there. If it ever gets tight, the cheaper options are already mapped out: a leaner model loader, trimming the vocabulary to the languages the groups actually use, or moving the model to its own container.

## Answering, not just deleting

The same groups kept asking the same questions, so in October I added ZyroMod, a support mode. An admin
sends the group's FAQ and product files to the bot in a private chat (`/zyromod`: text, markdown or
PDF, 10 MB per group) and sets a support email plus, optionally, any contact link with their own
button text. Members ask with `/ask`, by tagging the bot or by replying to it, and get an answer
taken only from those files. When the files do not cover it, they get the group's own "can't
answer" message with the email and the button. Each group can also give the bot its own
instructions (the tone, a docs link to end on, topics to decline), which shape the answer but
cannot widen what it knows.

It reuses the meaning model, so it costs no extra memory, and it follows the bot's usual rule of
never letting the AI act on its own: Claude only sees the matching sections, must answer from them
or say it cannot, and any link it writes that is not in the files is removed before sending. The
surprise came from calibration. A question only scores 0.20 to 0.65 against the section that
answers it, because a question and its answer do not mean the same thing, while unrelated chat
scores under 0.20. A strict cut-off would have refused 7 of 22 answerable test questions, so the
search only filters out the clearly unrelated and Claude decides the rest: a needless AI call costs
about 2 cents, a wrong "can't answer" costs a customer.

## What broke

**An adult-channel forward walked straight through.** On day 11, a member forwarded a post from an adult channel: a list of words written with ❌ emoji in place of letters, and an "OPEN HERE" button that linked out. Three things failed at once. The rules only read the message text, not the button or the name of the channel it came from. The emoji letters did not match anything. And the sender had joined before the bot was added, so the bot treated them as a regular. The fix: the text normalizer now maps emoji letters and lookalike characters back to plain letters, the rules read buttons and the forward source, a member with no recorded join counts as new until they have posted a few messages, and forwards from outside channels now need a captcha.

**Topics broke reply-based commands.** In a group with Topics, Telegram marks every message as a reply to the topic's first post. So an admin replying to a spammer with /ban would have pointed the bot at whoever opened the topic, and the bot's notices landed in the general chat instead of the topic. Now the bot ignores that automatic reply and posts in the right topic. Nobody was hit by this, but the 52,000-member group uses Topics, so it was one admin command away.

**The rules only spoke English.** On day 19 I tested the USDT scam in other languages. In English it scored 14, well past the auto-act line. In Russian, Chinese, Indonesian and Vietnamese it scored 0 to 2, so the judge never even saw it. Chinese missed the word "USDT" completely, because there is no word boundary between Chinese characters and Latin letters. The first fix was rules for the scam phrasing in seven more languages and per-group language settings. The meaning match is the general fix, for languages nobody wrote rules for.

**A fake-admin campaign that only the AI caught.** A read-only health check on day 23 found five posts in six days from accounts named like the community itself, with a lowercase L in place of a capital I. They announced that the token's contract address was moving and asked people to DM their wallet, one of them in Arabic. The judge flagged all five at 97 to 98% sure, which is the system working. But the rules had not recognized them, and a wallet-drain attempt was getting a 24-hour mute as its first strike. Now the rules catch the "contract is moving / old tokens will be burned / DM to register" pitch and ban for it, sure verdicts of impersonation skip the warning mute, and each group can list protected names so a lookalike account is removed the moment it joins.

**The same bug that once killed a guard in another project.** While adding those rules, a patch script wrote `\b` (a regex word boundary) into the rules file as a literal backspace character. That exact bug once disabled a safety check in my Gmail assistant for two months. This time a test failed before anything shipped, which is why every rule change runs the full suite first.

**Tagging the bot turned commands into questions.** A production log check the morning after ZyroMod went live showed `/spam` logged as an unanswered support question. Admins had typed `@zyroguard_bot /spam`, and Telegram only counts a command at the very start of a message, so the bot read it as a member asking something. A small middleware now moves the command to the front before anything else sees the message, and a test feeds that exact update through the real dispatcher.

**Still open:** the meaning match starts in shadow in every group and only counts once that group's admins turn it on after reviewing what it recorded. In the test it missed one wording of the contract scam ("our contract is migrating to V2, message me", 0.60), which the rules also miss, so that example goes into the library when it shows up. Scam images are judged one by one by the AI safety check; recognizing a recycled scam picture is a later step.

## Security

The bot holds ban rights in groups it does not own, so it is built to fail closed:

- Buttons are only accepted if that button exists on that exact message, so a forged callback cannot trigger an undo or a mode switch.
- The bot token is redacted from every logged error.
- Each group owner gets a daily AI cap, member questions to the bot are capped per day, and the picture safety check has its own cap, so nobody can spend the AI budget by spamming.
- The meaning match runs locally, and if the model ever fails to load the bot carries on with its rules and judge.
- The database is backed up every night and kept for 14 days. Every deploy tags the previous image so a rollback is one command.
- Production checks are read-only. They open the database in read-only mode and send no messages, and model tests run in a throwaway container with the database mounted read-only.
- Support files are read in memory and never saved as files; only their text is kept, per group. Files with passwords or keys in them are refused, and only the group's owner-level admins can change where members are sent for help, with every change reported.
- 443 tests, including end-to-end runs of the whole pipeline against a fake Telegram, plus a check that walks a brand-new group through every feature on the live version with the real AI (31 of 31 passed on 2026-10-14).

## What the numbers do not say

There is no measured baseline from before the bot, only the admins' account of the daily scams. "Zero undone" is a lower bound: an admin could unban someone by hand without using Undo. The numbers come from one large group; the two smaller groups were quiet. And the meaning-match results come from a 25-message test, not live traffic, which is why every group starts it in shadow. ZyroMod went live on 2026-10-13 and has no client numbers yet; its thresholds come from a 32-question test on a sample FAQ.

## What I would tell another builder

Let the AI be the tiebreaker, not the judge, jury and executioner. Rules that know a seed-phrase request on sight are cheaper and more predictable than any model. Then test your rules in every language your spammers use, not just the one you write in. And when you add a new layer, even a clever one, ship it in shadow and make it earn its vote.
