"""'@zyroguard_bot /spam' must run /spam (2026-10-14). Telegram only treats a command at the very start of
a message as a command; before this fix the mention made it a question for support mode instead."""
from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, Update

from zyroguard.assistant import MentionedCommands, unmention_command

CHAT = -1001234567890


def _update(text: str, entities: list[dict], reply: bool = True) -> dict:
    msg = {
        "message_id": 42, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": CHAT, "type": "supergroup", "title": "Test group"},
        "from": {"id": 111, "is_bot": False, "first_name": "Ana", "username": "ana"},
        "text": text, "entities": entities,
    }
    if reply:
        msg["reply_to_message"] = {"message_id": 41, "date": msg["date"], "chat": msg["chat"],
                                   "from": {"id": 555, "is_bot": False, "first_name": "Spam"},
                                   "text": "Earn 500 USD daily, DM me"}
    return {"update_id": 1, "message": msg}


def test_unmention_command_moves_the_command_to_the_front():
    text = "@ZyroGuardBot /spam all"
    m = Message.model_validate(_update(text, [{"type": "mention", "offset": 0, "length": 13},
                                             {"type": "bot_command", "offset": 14, "length": 5}])["message"])
    fixed = unmention_command(m, "zyroguardbot")   # usernames match case-insensitively
    assert fixed.text == "/spam all" and fixed.reply_to_message.message_id == 41
    assert [(e.type, e.offset, e.length) for e in fixed.entities] == [("bot_command", 0, 5)]
    assert m.text == text   # the original is untouched
    # left alone: other bots, plain mentions, paths, no mention at all
    for other in ["@SomeOtherBot /spam", "@ZyroGuardBot what is /spam?", "@ZyroGuardBot /usr/bin is missing",
                  "/spam", "hello @ZyroGuardBot /spam"]:
        assert unmention_command(Message.model_validate(_update(other, [])["message"]), "ZyroGuardBot") is None


async def test_the_dispatcher_runs_the_command_when_the_bot_is_tagged_first():
    dp = Dispatcher()
    dp["app"] = SimpleNamespace(bot_username="ZyroGuardBot")
    dp.message.outer_middleware(MentionedCommands())
    seen: list[tuple[str, CommandObject]] = []
    bots: list = []
    router = Router()

    @router.message(Command("spam"))
    async def spam(m: Message, command: CommandObject) -> None:
        seen.append((m.text, command))
        bots.append(m.bot)   # still bound to the bot, so replies and deletes work

    @router.message()
    async def anything_else(m: Message) -> None:
        seen.append((m.text, None))
    dp.include_router(router)
    bot = Bot("123:abc")
    try:
        await dp.feed_update(bot, Update.model_validate(_update("@ZyroGuardBot /spam", []), context={"bot": bot}))
        await dp.feed_update(bot, Update.model_validate(_update("@ZyroGuardBot is this spam?", []),
                                                        context={"bot": bot}))
    finally:
        await bot.session.close()
    assert seen[0][0] == "/spam" and seen[0][1].command == "spam" and seen[0][1].args is None
    assert seen[1] == ("@ZyroGuardBot is this spam?", None)   # a normal question is not touched
    assert bots == [bot]
