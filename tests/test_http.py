"""The internal HTTP API. /stats lists every group, so it needs the shared secret and is off without one."""

from __future__ import annotations

import dataclasses

from aiohttp.test_utils import TestClient, TestServer

from tests.test_pipeline_e2e import app  # noqa: F401  (pytest fixture)
from zyroguard import http


async def _get(app, path: str, headers: dict | None = None):  # noqa: F811
    client = TestClient(TestServer(http.build(app)))
    await client.start_server()
    try:
        r = await client.get(path, headers=headers or {})
        return r.status, await r.json()
    finally:
        await client.close()


async def test_stats_is_off_when_no_secret_is_set(app):  # noqa: F811
    app.config = dataclasses.replace(app.config, n8n_event_secret=None)
    for headers in (None, {"X-ZyroGuard-Secret": ""}, {"X-ZyroGuard-Secret": "anything"}):
        status, body = await _get(app, "/stats", headers)
        assert status == 401 and "groups" not in body


async def test_stats_needs_the_exact_secret(app):  # noqa: F811
    app.config = dataclasses.replace(app.config, n8n_event_secret="s3cret-value")
    assert (await _get(app, "/stats"))[0] == 401
    assert (await _get(app, "/stats", {"X-ZyroGuard-Secret": "s3cret-valu"}))[0] == 401
    status, body = await _get(app, "/stats", {"X-ZyroGuard-Secret": "s3cret-value"})
    assert status == 200 and "groups" in body
