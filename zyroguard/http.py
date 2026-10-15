"""Tiny internal HTTP API for n8n: /health and /stats. Bind it to the docker
network only, never publish the port. /stats lists every group, so it answers only
requests with the X-ZyroGuard-Secret header (N8N_EVENT_SECRET) and is off when no
secret is set."""

from __future__ import annotations

import hmac
from datetime import timedelta

from aiohttp import web

from .app import App
from .config import VERSION
from .durations import DurationError, parse_duration
from .store import utcnow


def build(app: App) -> web.Application:
    web_app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({
            "ok": True, "version": VERSION,
            "uptime_s": int(app.uptime.total_seconds()),
            "last_update_at": app.last_update_at.isoformat() if app.last_update_at else None,
            "db_writable": await app.store.writable(),
            "judge_configured": app.judge.configured,
        })

    async def stats(req: web.Request) -> web.Response:
        secret = app.config.n8n_event_secret
        given = req.headers.get("X-ZyroGuard-Secret", "")
        if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            d = parse_duration(req.query.get("since", "24h"))
        except DurationError:
            return web.json_response({"error": "bad since"}, status=400)
        chat_raw = req.query.get("chat_id")
        chat_id = int(chat_raw) if chat_raw and chat_raw.lstrip("-").isdigit() else None
        data = await app.store.stats(chat_id, utcnow() - timedelta(seconds=d.seconds or 86400))
        if chat_id is None:
            data["groups"] = [
                {"chat_id": int(c["chat_id"]), "title": c["title"],
                 "mode": (await app.settings.get(int(c["chat_id"]))).raw("mode")}
                for c in await app.store.list_chats()
            ]
        return web.json_response(data)

    web_app.router.add_get("/health", health)
    web_app.router.add_get("/stats", stats)
    return web_app


async def start(app: App) -> web.AppRunner:
    runner = web.AppRunner(build(app), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, app.config.http_host, app.config.http_port)
    await site.start()
    return runner
