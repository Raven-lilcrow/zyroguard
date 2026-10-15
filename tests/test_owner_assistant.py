"""The assistant knows the owner commands only when a bot owner asks in a private chat."""
from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.types import Message

from zyroguard import assistant
from zyroguard.help import COMMANDS

from .test_assistant import FakeMessages
from .test_pipeline_e2e import ADMIN, app, msg  # noqa: F401

STRANGER = 4242


def _private(text: str, uid: int) -> Message:
    return Message.model_validate({
        "message_id": 5, "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {"id": uid, "type": "private", "first_name": "A"},
        "from": {"id": uid, "is_bot": False, "first_name": "A", "username": "someone"}, "text": text})


def _wire(app, monkeypatch):  # noqa: F811
    fake = FakeMessages("Here you go.")
    app.assistant = assistant.Assistant(app.store, SimpleNamespace(messages=fake), "claude-opus-5", "medium")
    replies = []

    async def reply(self, text, **kw):
        replies.append(text)
    monkeypatch.setattr(Message, "reply", reply)
    return fake, replies


def test_owner_guide_lists_every_owner_command_and_where_to_send_it():
    guide = assistant.owner_guide()
    owner_only = [n for n, (what, _, _) in COMMANDS.items() if what.startswith("Owner only.")]
    assert {"permit", "revoke", "permits", "purge", "groups", "usage"} <= set(owner_only)
    for name in owner_only + ["reloadrules"]:
        assert f"`/{name}" in guide, name
    # the group team commands belong to users now, not to the owner section
    for name in ("addadmin", "removeadmin", "addmod", "removemod", "showadmin"):
        assert name not in owner_only and f"`/{name}" not in guide, name
    assert "`/purge <group id>`: Deletes all records of a group I already left." in guide
    assert "Owner only." not in guide


def test_only_the_owner_prompt_has_the_owner_section():
    normal, owner = assistant.build_system_prompt(), assistant.build_system_prompt(owner=True)
    assert owner.startswith(normal)
    assert "Owner commands" in owner and "`/purge <group id>`" in owner
    assert "Owner commands" not in normal and "/purge" not in normal and "/permit" not in normal


async def test_owner_in_a_private_chat_gets_the_owner_guide(app, monkeypatch):  # noqa: F811
    fake, replies = _wire(app, monkeypatch)
    await assistant.answer(app, _private("list the owner commands I can use", ADMIN), "list the owner commands I can use")
    assert "The person asking is a bot owner" in fake.calls[-1]["system"][0]["text"] and replies == ["Here you go."]


async def test_anyone_else_in_a_private_chat_does_not(app, monkeypatch):  # noqa: F811
    fake, _ = _wire(app, monkeypatch)
    await assistant.answer(app, _private("list the owner commands", STRANGER), "list the owner commands")
    assert "Owner commands" not in fake.calls[-1]["system"][0]["text"]


async def test_the_owner_asking_in_a_group_does_not_either(app, monkeypatch):  # noqa: F811
    fake, _ = _wire(app, monkeypatch)
    await assistant.answer(app, msg("@ZyroGuardBot what can owners do?", user_id=ADMIN), "what can owners do?")
    assert fake.calls and "Owner commands" not in fake.calls[-1]["system"][0]["text"]
