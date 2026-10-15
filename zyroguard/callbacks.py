"""Guard for inline-button callbacks.

Telegram does not check that the callback data a client sends belongs to a button
on the message it names, so anyone can replay "undo:12" or "h:start" against any
message of ours that has a keyboard (a captcha prompt in some other group, say)
and make the bot post or edit there. The fix is simple: only accept a callback
whose data is literally one of the buttons on that message.
"""

from __future__ import annotations

from aiogram.types import CallbackQuery

EXPIRED = "This button is no longer valid."


def genuine(cb: CallbackQuery) -> bool:
    m = cb.message
    markup = getattr(m, "reply_markup", None)
    rows = getattr(markup, "inline_keyboard", None) or []
    return any(getattr(b, "callback_data", None) == cb.data for row in rows for b in row)


async def reject(cb: CallbackQuery) -> None:
    try:
        await cb.answer(EXPIRED, show_alert=True)
    except Exception:  # noqa: BLE001
        pass
