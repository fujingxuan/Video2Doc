from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from app.db.session import SessionLocal
from app.services.tasks import TaskService


def page_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
    before = {item["id"]: item for item in previous["page"]["items"]}
    after = {item["id"]: item for item in current["page"]["items"]}
    upserts = [item for key, item in after.items() if before.get(key) != item]
    removed = [key for key in before if key not in after]
    page = current["page"]
    order = list(after)
    metadata = {key: page[key] for key in ("total", "offset", "limit")}
    old_metadata = {key: previous["page"][key] for key in metadata}
    if (
        not upserts
        and not removed
        and order == list(before)
        and metadata == old_metadata
        and previous["queue"] == current["queue"]
    ):
        return None
    return {
        "upserts": upserts,
        "removed_ids": removed,
        "order": order,
        **metadata,
        "queue": current["queue"],
    }


async def paged_event_stream(**query: Any) -> AsyncIterator[str]:
    # Every connection starts with a bounded snapshot. Reconnects resynchronize
    # the page, so no unbounded per-client cache or durable replay log is needed.
    previous: dict[str, Any] | None = None
    while True:
        # SQLite and serialization are synchronous work: keep them off the
        # event loop that also drives media cancellation and HTTP providers.
        current = await asyncio.to_thread(_snapshot, query)
        if previous is None:
            event, payload = "page", current
        else:
            event, payload = "delta", page_delta(previous, current)
        if payload is None:
            yield ": keep-alive\n\n"
        else:
            yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        previous = current
        await asyncio.sleep(1.0)


def _snapshot(query: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as session:
        service = TaskService(session)
        return {
            "page": service.page(**query).model_dump(mode="json"),
            "queue": service.queue_status().model_dump(mode="json"),
        }
