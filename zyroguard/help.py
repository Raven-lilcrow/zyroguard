"""/start and /help: the introduction with the four main features, then layered topic pages,
written for admins who are not developers."""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .app import App

router = Router(name="help")

# /start and /help both open with this: who I am and my four main features
OVERVIEW = """👋 <b>ZyroGuard</b>
<b>I keep Telegram groups free of spam and scams, and keep the chat clean.</b>

<b>Spam and phishing protection</b>
Scam links, fake jobs, investment pitches and account phishing are removed. An AI judge checks the unclear ones.

<b>Group cleaning</b>
New members pass a quick captcha. Floods, links from newcomers, and explicit or abusive posts are cleaned up.

<b>Teaching by example</b>
Admins show me spam with /spam. I learn what it means, so I can spot reworded copies, even in other languages.

<b>Admin tools</b>
Ban, mute, warn and undo. Everything I do is reported to the admins with an Undo button.

Tap a feature to see how it works. Have a question? Send /ask and your question."""

# button labels and the topic each one opens, laid out in rows
MENU_ROWS = [
    [("Spam and phishing", "protect"), ("Group cleaning", "cleaning")],
    [("Teaching", "teach"), ("Admin tools", "admin")],
    [("Set me up", "start"), ("Support mode", "support")],
]
# the detailed pages under each feature, as buttons; Back on a detailed page returns to its feature
DETAILS = {
    "protect": [("Your own rules", "rules"), ("Trusted people and sites", "trust"), ("Test me", "check")],
    "cleaning": [("Safety check", "safety")],
    "admin": [("From our private chat", "private"), ("All settings", "settings"), ("How to write times", "time")],
}
PARENT = {key: feature for feature, pages in DETAILS.items() for _, key in pages}


def help_markup(topic: str | None) -> InlineKeyboardMarkup:
    if topic is None:
        rows = [[InlineKeyboardButton(text=t, callback_data=f"h:{k}") for t, k in row] for row in MENU_ROWS]
        rows.append([InlineKeyboardButton(text="Close", callback_data="h:x")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    pages = DETAILS.get(topic, [])
    rows = [[InlineKeyboardButton(text=t, callback_data=f"h:{k}") for t, k in pages[i:i + 2]]
            for i in range(0, len(pages), 2)]
    rows.append([InlineKeyboardButton(text="« Back", callback_data=f"h:{PARENT.get(topic, '_')}"),
                 InlineKeyboardButton(text="Close", callback_data="h:x")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

TOPICS = {
    "protect": """<b>Spam and phishing protection</b>
<b>What I remove</b>
• Scam and phishing links, fake login pages and wallet drainers
• Investment, crypto and get-rich-quick pitches
• Fake jobs and task scams, like being paid to like videos or rate apps
• Account phishing: asking for login codes, or fake "your account will be suspended" warnings
• Fake support accounts that copy an admin's name
• People on the known spammer list (CAS) when they join

<b>How I decide</b>
1. Every message is checked against my spam rules.
2. Unclear ones go to an AI judge, which reads them the way a person would.
3. Spam is deleted. Repeat spammers are muted, then banned.

<b>Start safely</b>
I start in shadow mode: I only report what I would do. When the reports look right:
/mode enforce – really delete and punish
/test free USDT, DM me – what I would do with a text. Nothing is deleted.
/set judge_context a coffee shop community – tells the AI judge what's normal in your group""",
    "cleaning": """<b>Group cleaning</b>
• New members pass a quick captcha (pick the word for a picture) before they can talk. Most spam bots never get past it.
• For their first 24 hours, new members can't post links or forwards, and their pictures go through the safety check.
• "Joined" and "left" messages are removed.
• Flooding (more than 5 messages in 10 seconds) gets the sender muted for an hour.
• Explicit, violent, bullying or hateful images and messages are removed. This is the safety check.
• My own notices in the group disappear after 10 minutes.

<b>Change it</b>
/setcaptchatime 2m – how long new members have to pass the captcha
/set captcha off – no captcha
/setrestrict 24h – how long the limits on new members last (0 = none)
/setflood 5 10s – what counts as flooding. /setfloodmode mute 1h – what happens then
/setsafety off – turn the safety check off
/set clean_service off – keep the joined and left messages
/set notice_ttl 10m – how long my notices stay (0 = keep them)""",
    "teach": """<b>Teaching by example</b>
<b>How to teach me</b> (admins)
• Reply to the spam with /spam. I save it and remove it. Type /spam in the reply yourself: picking it from the command menu sends it without the reply.
• Already removed by me? Tap Teach as spam on my report, or send /spam with the action number from /log.
• A normal message I keep flagging? Reply /notspam to it. /undo teaches me that too.

/spam 412 – teach me the message from action 412
/examples – what I learned, and whether it counts yet
/forget 14 – remove example 14

<b>Starting out</b>
Teaching starts in a trial: I record what my examples would have changed, but don't act on them yet. When that looks right, an admin sends /set similarity on and the examples start counting.
Even then a match never punishes anyone by itself. It sends the message to the AI judge, which decides.""",
    "admin": """<b>Admin tools</b>
Reply to someone's message with the command, or add their @username.
/ban 7d spam links – ban. No time means forever.
/mute 2h – they can read but not write
/kick – remove them. They can rejoin.
/warn reason – give a strike. At 3 warns they are muted for a day.
/unban @user – let them back. /unmute @user – let them write again.
/strikes @user – see their strikes. /pardon @user – clear them.

<b>Everything I do is on record</b>
/log – my last actions, with their numbers
/why 12 – the full story of action 12
/undo 12 – reverse it
/stats 7d – what I caught lately
/diag – health check, with a fix for anything that is off

<b>Your team</b>
/showadmin – who has power in this group
/addadmin @user – let someone use these commands here, even if they are not a Telegram admin
/removeadmin @user – take it back
Adding and removing them: the person who added me, the group's creator, or admins who can add new admins.

Members can reply /report to a message to flag it for you.
All of these work in our private chat too, so members never see you set me up.""",
    "start": """<b>Getting started</b>
1. Add me to your group.
2. Make me an admin. Turn on: Delete messages, Ban users, Invite users via link.
3. Send /diag in the group, or in our private chat. It tells you if anything is missing and how to fix it.
4. I report every action to you in our private chat. Prefer a log group? Add me to a private group, send /id there, then send /set modlog_chat_id followed by that id.
5. I start in <b>shadow</b> mode: I only report what I would do. Watch the reports for a day or two.
6. When you are happy, send /mode enforce. From then on I really delete and punish.
7. All admin commands also work in our private chat, so members never see you configure me. See /help private.""",
    "private": """<b>Managing your group from our private chat</b>
Every admin command works here too: /settings, /diag, /mode, /log, /stats, /rule, /set and the rest.
Commands sent here apply to the group you are working on.

/use – shows your groups, tap one to work on it. With one group I pick it for you.
/settings – the button menu for that group.
/diag – health check for that group.
/log – my last actions there, with undo numbers.

My reports about that group also arrive here, unless you set a log group with /set modlog_chat_id.
Things that need a message to reply to (like /ban as a reply) and /report still happen in the group.""",
    "punish": """<b>Punishing someone</b>
Reply to their message with the command, or write the command and their @username.

/ban [time] [reason] – ban. No time means forever. Example: /ban @user 7d scam links
/mute [time] – they can read but not write. Example: /mute 2h
/kick – remove them. They can come back (and get the captcha again).
/warn [reason] – give a strike. As a reply it also deletes that message. At the warn limit (3 by default) the warn action fires, see /setwarnlimit and /setwarnmode.
/unban @user – let a banned user back.
/unmute @user – let a muted user write again.
/strikes @user – how many strikes they have. /pardon @user – clear them.
/undo &lt;number&gt; – reverse anything I did. The number is in every report and in /log.
/report – reply to a message to send it to the admins' reports for a human to look at.""",
    "trust": """<b>People and links I should never touch</b>
/allow @user – I skip all my checks for them. /disallow @user takes it back.
/allowed – shows everyone and every website on the lists.
/allowdomain example.com – links to that site are always fine.
/blockdomain example.com – links to that site are always removed.

Group admins are never checked.
/addmod @user – the same as /allow. /removemod @user – the same as /disallow.

<b>Your team</b>
/addadmin @user – let someone use my admin commands here, even if they are not a Telegram admin. /removeadmin @user takes it back.
Only the person who added me, the group's creator, or admins who can add new admins can add or remove them.
/showadmin – who has power in this group.""",
    "rules": """<b>Your own spam patterns</b>
/rule add &lt;words or pattern&gt; [what to do]
Examples:
/rule add free crypto giveaway
/rule add (?i)dm me for signals ban 30d
If you do not say what to do, the normal punishments apply (mute, then ban).

/rule list – see them with their numbers. /rule del &lt;number&gt; – remove one.
Patterns are case-insensitive. They can be plain words or regular expressions.""",
    "settings": """<b>Changing how strict I am</b>
<b>The basics</b>
/settings – a menu with buttons, sent to your private chat with me.
/settings list – every setting as text, with its name in grey.
/set &lt;name&gt; &lt;value&gt; – change one. Example: /set captcha off
/unset &lt;name&gt; – back to the default.

<b>Mode</b>
/mode shadow – report only, nothing is deleted
/mode enforce – really delete and punish
/pause 30m – stop filtering for a while, /resume – start again

<b>New members</b>
/setcaptchatime 2m – how long new members have to pass the captcha
/set captcha_mode emoji – emoji = pick the word for a picture (default), math = a small sum, button = just tap
/setcaptchafail kick – what happens when they do not pass in time (kick, or ban with a time)
/set captcha_every_join off – only ask the captcha the first time someone joins (default: every time, even after leaving)
/setrestrict 24h – new members cannot post links or media for this long
/setnewaccount 1h ban 30d – harsher action for spam from people who joined less than 1h ago
/setcasmode ban – what happens to people on the known spammer list
/setgreeting Welcome {name}! – sent after someone passes the captcha

<b>Strikes and floods</b>
/setstrike 1 mute 24h – what the 1st strike does (also 2 and 3)
/setstriketime 30d – how long strikes are remembered
/setwarnlimit 3 – how many /warn before the warn action, /setwarnmode mute 1d – what it does
/setflood 5 10s – more than 5 messages in 10 seconds is flooding
/setfloodmode mute 1h – what happens to a flooder

<b>AI judge and safety check</b>
/set judge_context a crypto trading community, coin talk is normal – tells the AI judge what is normal here, so it flags less of your real chat
/setjudgemode delete – what happens when the AI judge says spam
/setsafetymode mute 1d – what happens after a failed safety check

<b>Notices in the group</b>
/set notice_ttl 10m – my notices, greetings and command replies in the group disappear after this (0 = keep)
/set group_notice off – no line in the group when I remove spam (reports still go to your log)""",
    "time": """<b>How to write times</b>
30m = 30 minutes, 2h = 2 hours, 7d = 7 days, 2w = 2 weeks.
You can combine them: 1d12h. 0 or forever means permanent.
Examples: /mute @user 2h, /ban 7d, /setstrike 2 ban 30d, /pause 45m""",
    "safety": """<b>Safety check</b>
Spam is not the only problem. I also remove content that does not belong in a normal group:
• explicit photos or stickers (nudity, porn)
• scam pitches written on images (crypto offers, fake jobs, giveaways, wallet QR codes)
• gore and threats of violence
• bullying, telling someone to hurt themselves
• slurs and hate against a group of people

How it works, in plain terms:
1. Every photo, sticker or video from a normal member is shown to the AI, which also reads any text written on the image.
2. A text message is only checked when it contains a harsh word or a threat, so everyday chat costs nothing.
3. If the AI is at least 85% sure, I delete the message and the sender is muted for 1 day. A scam on an image gets the spam punishment instead (strike ladder).
4. You get a report with the reason and an Undo button, like every other action.

Admins and trusted users are never checked. Casual swearing between friends is left alone.

/setsafety off – turn it off for this group. /setsafety on – back on.
/setsafetymode ban 7d – change what happens to the sender. Any action works: delete, kick, mute 1d, ban 7d, ban.
/setsafety – shows the current setting.
These checks share the daily limit of the spam judge (judge_daily_cap).""",
    "support": """<b>Support mode: answers from your own files</b>
Give me your FAQ and product files and I'll answer members' questions from them. When your files don't have the answer, I send members to your team.

<b>Set it up in our private chat</b>
/zyromod – opens the support menu for your group.
1. Send your files: .txt, .md or .pdf, up to 10 MB per group.
2. Set a support email. Members get it when I can't answer.
3. Optional: a contact button that opens any link (ticket page, Discord, website), with your own button text.
4. Optional: instructions for me. My tone, what to do and not do, and links or details to always include, like your docs page.
5. Tap Test a question, then Turn on.

<b>In the group</b>
Members ask with /ask and their question, by @mentioning me, or by replying to my answer. Someone who asks a plain question without tagging me gets a short reminder to use /ask, at most once a day.
Admins can reply /ask to a member's question to answer it from your files.
I only answer from your files and never make up prices, dates or links. Anything else gets your can't-answer message with the email and the button.

<b>Keep it safe</b>
Only send what you'd put on a public help page. Members can see anything in these files.
Files with passwords or keys in them are refused.
Only the group's creator, admins who can add admins, or the person who added me can change the email, button, message and instructions. Every change is posted to your reports.
The menu lists the questions I couldn't answer, so you know what to add to your files.

/set support off – stop answering from files.
/set support_hint off – no reminders to use /ask.""",
    "check": """<b>Seeing what I did, and testing me</b>
/diag – health check. Tells you exactly what to fix if something is off.
/log – my last actions with their numbers. /why &lt;number&gt; – the full story of one action.
/test &lt;some text&gt; – I say what I would do with that text. Nothing is deleted.
/stats 7d – how much I caught in the last 7 days.
/id – ids of this chat and you.

<b>Asking me questions</b>
@mention me, reply to one of my messages, or send /ask followed by the question. In a private chat just write.
I can explain things, give step-by-step guides, and help with my own commands. I cannot look things up online or see live data.
With support mode on, /ask answers come only from your own files, see /help support.
I remember our last few questions for 30 minutes so follow-ups work. /clear makes me forget them (bans and strikes are never touched).
Admins: /set assistant off turns this off, /set assistant_daily_cap 100 limits it, /clear all forgets everyone's questions.""",
}


def _codify(text: str) -> str:
    """Lines that start with a command show the command as <code>command</code> before the description, so a tap
    copies the whole command instead of Telegram highlighting just the /word."""
    out = []
    for line in text.split("\n"):
        if line.startswith("/"):
            cmd, sep, rest = line.partition(" – ")
            line = f"<code>{cmd}</code>{sep}{rest}" if sep else f"<code>{line}</code>"
        out.append(line)
    return "\n".join(out)


TOPIC_LEADS = {
    "protect": "Scams are removed before most members see them.",
    "cleaning": "Keeps the chat tidy without an admin watching all day.",
    "teach": "Show me spam once, and I recognize messages that mean the same thing, in about 50 languages.",
    "admin": "Ban, mute, warn and undo. Every action I take comes with a report and an Undo button.",
    "start": "Seven steps from adding me to enforce mode.",
    "private": "Every admin command works in our private chat, so members never see you configure me.",
    "punish": "Ban, mute, kick or warn someone, and undo any of it.",
    "trust": "Trusted people and websites skip every check.",
    "rules": "Add your own spam patterns, with or without their own punishment.",
    "settings": "Everything has a default. Change only what you need.",
    "time": "The same short format everywhere.",
    "safety": "Explicit, violent and abusive content is removed too, not only spam.",
    "check": "See what I did, why, and try me out without touching the group.",
    "support": "Members ask, I answer from your FAQ files, and send the rest to your team.",
}


def _restyle(key: str, text: str) -> str:
    first, _, rest = text.partition("\n")
    title = first.replace("<b>", "").replace("</b>", "")
    lead = TOPIC_LEADS.get(key, "")
    head = f"📖 <b>ZyroGuard</b> · {title}"
    return f"{head}\n<b>{lead}</b>\n\n{rest}" if lead else f"{head}\n\n{rest}"


TOPICS = {k: _restyle(k, _codify(v)) for k, v in TOPICS.items()}
TOPICS["setup"] = TOPICS["start"]
TOPICS["moderation"] = TOPICS["admin"]
TOPICS["phishing"] = TOPICS["protect"]
TOPICS["teaching"] = TOPICS["teach"]
TOPICS["cleanup"] = TOPICS["cleaning"]
TOPICS["lists"] = TOPICS["trust"]
TOPICS["durations"] = TOPICS["time"]
TOPICS["troubleshooting"] = TOPICS["check"]
TOPICS["modes"] = TOPICS["settings"]

# for each command: (what it does, how to use it, example)
COMMANDS: dict[str, tuple[str, str, str]] = {
    "ban": ("Bans the user. Without a time it is forever. Timed bans end on their own.",
            "Reply to their message with /ban [time] [reason], or /ban @user [time] [reason]", "/ban @user 7d scam links"),
    "mute": ("They can read but not write. Without a time it is forever.", "Reply with /mute [time], or /mute @user [time]", "/mute 2h"),
    "kick": ("Removes the user. They can rejoin and will get the captcha again.", "Reply with /kick, or /kick @user", "/kick @user"),
    "warn": ("Gives a strike. Used as a reply it also deletes that message. At 3 warns they get muted for a day.", "Reply with /warn [reason]", "/warn no ads please"),
    "unban": ("Lets a banned user come back.", "/unban @user or /unban <id>", "/unban @user"),
    "unmute": ("Lets a muted user write again. Also fixes someone stuck on the captcha.", "/unmute @user", "/unmute @user"),
    "strikes": ("Shows a user's strikes and when they expire.", "/strikes @user", "/strikes @user"),
    "pardon": ("Clears all of a user's strikes.", "/pardon @user", "/pardon @user"),
    "undo": ("Reverses something I did. Unbans, unmutes and removes the strike.", "/undo <action number>", "/undo 12"),
    "report": ("Sends a message to the admins' reports so a human can look at it.", "Reply to the message with /report", "/report"),
    "allow": ("I skip all my checks for this user.", "/allow @user", "/allow @user"),
    "disallow": ("Takes /allow back.", "/disallow @user", "/disallow @user"),
    "allowed": ("Shows trusted users and the link lists.", "/allowed", "/allowed"),
    "allowdomain": ("Links to this website are always fine.", "/allowdomain <site>", "/allowdomain youtube.com"),
    "spam": ("Teaches me that a message is spam and removes it. Once the meaning match is on (/set similarity on), "
             "messages that mean the same thing, in any language, score higher. It does not punish the sender: "
             "add /ban if you want that. For a message I already removed, use its action number, or tap "
             "Teach as spam on my report.",
             "Reply to the message with /spam, or /spam <action number> (owner: add all to teach every group)",
             "/spam 412"),
    "notspam": ("Teaches me that a message is fine, so similar messages count less as spam once the meaning match "
                "is on. /undo does this for you automatically.",
                "Reply to the message with /notspam, or /notspam <action number>", "/notspam 412"),
    "examples": ("Shows what I learned from /spam, /notspam, /undo and my own sure catches (those wait 24 hours "
                 "before they count), and whether the meaning match is on.",
                 "/examples", "/examples"),
    "forget": ("Removes one learned example.", "/forget <number from /examples>", "/forget 14"),
    "blockdomain": ("Links to this website are always removed.", "/blockdomain <site>", "/blockdomain scam-site.io"),
    "rule": ("Your own spam patterns. See /help rules.", "/rule add <pattern> [action], /rule list, /rule del <number>", "/rule add (?i)dm me for signals ban 30d"),
    "set": ("Changes one setting. Names are shown in grey in /settings list.", "/set <name> <value>", "/set captcha off"),
    "unset": ("Puts a setting back to its default.", "/unset <name>", "/unset captcha"),
    "settings": ("Button menu in your private chat, or a text list.", "/settings or /settings list", "/settings list"),
    "mode": ("shadow = report only. enforce = really delete and punish.", "/mode shadow or /mode enforce", "/mode enforce"),
    "pause": ("Stops filtering for a while.", "/pause <time>", "/pause 30m"),
    "resume": ("Ends a pause early.", "/resume", "/resume"),
    "setstrike": ("What the 1st, 2nd or 3rd strike does.", "/setstrike <1|2|3> <action>", "/setstrike 1 mute 24h"),
    "setstriketime": ("How long strikes are remembered.", "/setstriketime <time>", "/setstriketime 30d"),
    "setwarnlimit": ("How many /warn before the warn action fires.", "/setwarnlimit <number>", "/setwarnlimit 3"),
    "setwarnmode": ("What happens at the warn limit.", "/setwarnmode <action>", "/setwarnmode mute 1d"),
    "setflood": ("How many messages in how many seconds count as flooding. 0 turns it off.", "/setflood <count> [window]", "/setflood 5 10s"),
    "setfloodmode": ("What happens to a flooder.", "/setfloodmode <action>", "/setfloodmode mute 1h"),
    "setnewaccount": ("Harsher action for spam from brand-new accounts.", "/setnewaccount <age> <action>", "/setnewaccount 1h ban 30d"),
    "setcaptchatime": ("How long a new member has to tap the button.", "/setcaptchatime <time>", "/setcaptchatime 2m"),
    "setcaptchafail": ("What happens when they do not tap in time.", "/setcaptchafail kick, or ban <time>", "/setcaptchafail ban 1d"),
    "setrestrict": ("New members cannot post links or media for this long. 0 turns it off.", "/setrestrict <time>", "/setrestrict 24h"),
    "setjudgemode": ("What happens when the AI judge says spam.", "/setjudgemode <action>", "/setjudgemode mute 12h"),
    "setcasmode": ("What happens to users on the CAS spam list.", "/setcasmode <action>", "/setcasmode ban"),
    "setgreeting": ("Message sent after someone passes the captcha. {name} becomes their name.", "/setgreeting <text>", "/setgreeting Welcome {name}! Read the pinned rules."),
    "zyromod": ("Support mode: I answer members' /ask questions from your own FAQ and product files, and send "
                "them to your team when the files do not have the answer. Files, contacts, testing and the list of "
                "unanswered questions all live here.", "In our private chat: /zyromod", "/zyromod"),
    "ask": ("Ask me a question or for a how-to guide. I cannot look things up online.", "/ask <question>, or reply to a message with /ask", "/ask how do I mute someone for an hour?"),
    "clear": ("Forgets the earlier questions between you and me, so the next one starts fresh and costs less. Admins: /clear all does it for everyone in the group. Bans, mutes, strikes and settings are never touched.", "/clear, or /clear all", "/clear"),
    "setsafety": ("Turns the safety check on or off for this group. It removes explicit, violent, bullying or hateful images and messages. Without a value it shows the current setting.", "/setsafety on, or /setsafety off", "/setsafety on"),
    "setsafetymode": ("What happens to someone whose message failed the safety check. The message itself is always deleted.", "/setsafetymode <action>", "/setsafetymode mute 1d"),
    "contact": ("Send a message to the person who runs ZyroGuard, for example to get it for your own group. Works in a private chat with me.", "/contact, then write your message", "/contact"),
    "diag": ("Health check with a fix for anything that is off.", "/diag", "/diag"),
    "test": ("Shows what I would do with a text. Nothing is deleted.", "/test <text>, or reply to a message with /test", "/test free USDT, DM me"),
    "why": ("The full story of one action.", "/why <action number>, or reply to the user's message with /why", "/why 12"),
    "log": ("My last actions with their numbers.", "/log [how many]", "/log 20"),
    "stats": ("How much I caught in a period.", "/stats [period]", "/stats 7d"),
    "id": ("Shows this chat's id, your id, and the id of a user you replied to.", "/id", "/id"),
    "addadmin": ("Lets someone use my admin commands in this group, even if they are not a Telegram admin. My filters "
                 "skip them too. Only the person who added me, the group's creator, or admins who can add new admins "
                 "can use it.", "/addadmin @user, or reply to them with /addadmin", "/addadmin @helper"),
    "removeadmin": ("Takes /addadmin back. Same people as /addadmin.", "/removeadmin @user", "/removeadmin @helper"),
    "addmod": ("Makes someone trusted here: my filters skip them, but they get no commands. The same as /allow.",
               "/addmod @user", "/addmod @helper"),
    "removemod": ("Takes /addmod back. The same as /disallow.", "/removemod @user", "/removemod @helper"),
    "showadmin": ("Shows who has power in this group: the person who added me, group admins, bot admins and "
                  "trusted users.", "/showadmin", "/showadmin"),
    "groups": ("Owner only. Every group I am in.", "/groups", "/groups"),
    "usage": ("Owner only. AI judge usage per group.", "/usage [period]", "/usage 7d"),
    "use": ("In our private chat: pick which of your groups the commands apply to.", "/use, or /use <group id>", "/use"),
    "permit": ("Owner only. Lets someone add me to their own group. Optional: how many groups, for how long, a note.", "/permit @user [groups] [time] [note]", "/permit @alice 2 30d paid plan"),
    "revoke": ("Owner only. Takes a permit back: I leave their groups and delete their records.", "/revoke @user or id", "/revoke @alice"),
    "permits": ("Owner only. Who has a permit and which groups they use.", "/permits", "/permits"),
    "purge": ("Owner only. Deletes all records of a group I already left.", "/purge <group id>", "/purge -1001234"),
    "history": ("Owner only. What happened lately: groups added or refused, access changes, what admins did and changed. Newest first, Manila time.", "/history [days]", "/history 30"),
    "ledger": ("Owner only. Is the activity sheet being updated? Add sync to send everything now.", "/ledger [sync]", "/ledger sync"),
}


def owner_only(name: str) -> bool:
    return name in COMMANDS and COMMANDS[name][0].startswith("Owner only.")


def command_help(name: str) -> str:
    what, how, example = COMMANDS[name]
    return (f"📖 <b>/{name}</b> · command\n<b>{html.escape(what)}</b>\n\n"
            f"How to use: <code>{html.escape(how)}</code>\nExample: <code>{html.escape(example)}</code>\n\n"
            "All commands by topic: /help")


def start_markup(app: App) -> InlineKeyboardMarkup:
    """The feature buttons, plus the way to reach the bot's owner (for people who want me in their group)."""
    from .contact import contact_button
    rows = help_markup(None).inline_keyboard
    return InlineKeyboardMarkup(inline_keyboard=rows[:-1] + contact_button(app).inline_keyboard + rows[-1:])


@router.message(CommandStart())
async def cmd_start(m: Message, app: App) -> None:
    """Same introduction as /help (any deep link that reaches here, like ?start=help, gets it too)."""
    if m.chat.type != ChatType.PRIVATE:
        await _deliver(m, app, OVERVIEW, help_markup(None))
        return
    from .contact import contact_line
    await m.answer(OVERVIEW + "\n\n" + contact_line(app), parse_mode="HTML", reply_markup=start_markup(app))


@router.message(Command("help"))
async def cmd_help(m: Message, app: App, command: CommandObject) -> None:
    arg = (command.args or "").strip().lstrip("/").lower()
    markup: InlineKeyboardMarkup | None
    if not arg:
        text, markup = OVERVIEW, help_markup(None)
    elif arg in TOPICS:
        text, markup = TOPICS[arg], help_markup(arg)
    elif arg in COMMANDS and (not owner_only(arg) or (m.from_user and app.config.is_owner(m.from_user.id))):
        text, markup = command_help(arg), None
    else:   # owner commands don't exist for anyone but the bot's owners
        text = (f"I have no help page called '{html.escape(arg)}'.\n"
                "Send /help on its own for the topic buttons, or /help followed by a command name.")
        markup = None
    await _deliver(m, app, text, markup)


async def _deliver(m: Message, app: App, text: str, markup: InlineKeyboardMarkup | None) -> None:
    if m.chat.type == ChatType.PRIVATE:
        await m.answer(text, parse_mode="HTML", reply_markup=markup)
        return
    # keep groups clean: try DM first
    try:
        if m.from_user:
            await app.bot.send_message(m.from_user.id, text, parse_mode="HTML", reply_markup=markup)
            await m.reply("Sent to your private chat with me.")
            return
    except Exception:  # noqa: BLE001
        pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Open a private chat with me", url=f"https://t.me/{app.bot_username}?start=help")]])
    await m.reply("I cannot message you privately yet. Open a chat with me, press Start, then send /help there.",
                  reply_markup=kb)


@router.callback_query(F.data.startswith("h:"))
async def cb_help(cb: CallbackQuery, app: App) -> None:
    from .callbacks import genuine, reject
    if not genuine(cb):
        await reject(cb)
        return
    key = cb.data.split(":", 1)[1]
    if key == "x":
        try:
            await cb.message.delete()
        except Exception:  # noqa: BLE001
            pass
        await cb.answer()
        return
    if key == "_":
        text, markup = OVERVIEW, help_markup(None)
    elif key in TOPICS:
        text, markup = TOPICS[key], help_markup(key)
    else:
        await cb.answer()
        return
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except Exception:  # noqa: BLE001
        pass
    await cb.answer()
