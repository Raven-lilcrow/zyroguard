"""A fake Telegram Bot API for the end-to-end checks in tools/. Nothing leaves the process.

Every API call is answered locally and recorded. Sent and edited messages are kept with their inline
buttons, so a test can press a button exactly like a user (the bot only accepts a press when that
button is on that message). Documents sent by a test user can be downloaded by the bot.
"""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone

from aiogram.client.session.base import BaseSession

ADMIN_RIGHTS = {"can_be_edited": False, "is_anonymous": False, "can_manage_chat": True, "can_delete_messages": True,
                "can_manage_video_chats": True, "can_restrict_members": True, "can_promote_members": False,
                "can_change_info": False, "can_invite_users": True, "can_post_stories": False,
                "can_edit_stories": False, "can_delete_stories": False, "can_send_welcome_messages": False}
SENDS = {"sendMessage", "sendPhoto", "sendAnimation", "sendDocument"}
EDITS = {"editMessageText", "editMessageReplyMarkup", "editMessageCaption"}


def now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class FakeTelegram(BaseSession):
    def __init__(self, groups: dict[int, dict], owner: dict, bot_user: dict):
        super().__init__()
        self.groups = groups            # chat id -> chat dict, for every group in the test
        self.owner = owner
        self.bot_user = bot_user
        self.log: list[tuple[str, dict, object]] = []
        self.messages: dict[tuple[int, int], dict] = {}   # what the bot sent, as it looks now
        self.deleted: set[tuple[int, int]] = set()
        self.files: dict[str, bytes] = {}
        self.alerts: list[str] = []     # texts of answerCallbackQuery (toasts and pop-ups)
        self.n = itertools.count(5000)

    def chat(self, chat_id) -> dict:
        if chat_id in self.groups:
            return self.groups[chat_id]
        return {"id": chat_id, "type": "private", "first_name": "User"}

    @staticmethod
    def _inline(markup) -> dict | None:
        return markup if isinstance(markup, dict) and "inline_keyboard" in markup else None

    def _result(self, name: str, p: dict):
        cid = p.get("chat_id")
        if name in SENDS:
            msg = {"message_id": next(self.n), "date": now(), "chat": self.chat(cid), "from": self.bot_user,
                   "text": p.get("text") or p.get("caption") or "(media)"}
            if self._inline(p.get("reply_markup")):
                msg["reply_markup"] = p["reply_markup"]
            self.messages[(cid, msg["message_id"])] = msg
            return msg
        if name in EDITS:
            key = (cid, p.get("message_id"))
            msg = dict(self.messages.get(key) or {"message_id": p.get("message_id"), "date": now(),
                                                  "chat": self.chat(cid), "from": self.bot_user, "text": ""})
            if "text" in p:
                msg["text"] = p["text"]
            msg.pop("reply_markup", None)
            if self._inline(p.get("reply_markup")):
                msg["reply_markup"] = p["reply_markup"]
            self.messages[key] = msg
            return msg
        if name == "deleteMessage":
            self.deleted.add((cid, p.get("message_id")))
            return True
        if name == "answerCallbackQuery":
            if p.get("text"):
                self.alerts.append(p["text"])
            return True
        if name == "copyMessage":
            return {"message_id": next(self.n)}
        if name == "getChatAdministrators":
            return [{"status": "creator", "user": self.owner, "is_anonymous": False},
                    {"status": "administrator", "user": self.bot_user, **ADMIN_RIGHTS}]
        if name == "getChatMember":
            uid = p.get("user_id")
            if uid == self.bot_user["id"]:
                return {"status": "administrator", "user": self.bot_user, **ADMIN_RIGHTS}
            if uid == self.owner["id"]:
                return {"status": "creator", "user": self.owner, "is_anonymous": False}
            return {"status": "member", "user": {"id": uid, "is_bot": False, "first_name": "Member"}}
        if name == "getChat":
            return {**self.chat(cid), "accent_color_id": 0, "max_reaction_count": 0,
                    "accepted_gift_types": {"unlimited_gifts": False, "limited_gifts": False, "unique_gifts": False,
                                            "premium_subscription": False, "gifts_from_channels": False}}
        if name == "getFile":
            fid = p.get("file_id")
            return {"file_id": fid, "file_unique_id": f"{fid}-u", "file_size": len(self.files.get(fid, b"")),
                    "file_path": f"documents/{fid}"}
        if name == "getMe":
            return self.bot_user
        if name == "getChatMemberCount":
            return 42
        return True

    async def make_request(self, bot, method, timeout=None):
        name = method.__api_method__
        p = method.model_dump(exclude_none=True)
        raw = self._result(name, p)
        self.log.append((name, p, raw))
        return self.check_response(bot, method, 200, json.dumps({"ok": True, "result": raw})).result

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.files[url.rsplit("/", 1)[-1]]

    async def close(self):
        pass

    # ---- helpers for tests ---------------------------------------------------------------------------

    def sent_since(self, mark: int, chat_id: int | None = None) -> list[str]:
        """Texts the bot sent or edited in after log position `mark` (optionally in one chat)."""
        return [p.get("text", "") for name, p, _ in self.log[mark:]
                if name in SENDS | {"editMessageText"} and (chat_id is None or p.get("chat_id") == chat_id)]

    def button(self, chat_id: int, label: str) -> tuple[dict, str] | None:
        """The newest message in a chat with a button whose text contains `label`: (message, callback data)."""
        for (cid, mid), msg in sorted(self.messages.items(), key=lambda kv: kv[0][1], reverse=True):
            if cid != chat_id or (cid, mid) in self.deleted:
                continue
            for row in (msg.get("reply_markup") or {}).get("inline_keyboard", []):
                for b in row:
                    if label.lower() in b.get("text", "").lower() and b.get("callback_data"):
                        return msg, b["callback_data"]
        return None
