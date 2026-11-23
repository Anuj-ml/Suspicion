"""Telegram transport. Long polling so no public HTTPS webhook is needed."""

import asyncio
import json
import logging
import time

import httpx

log = logging.getLogger("suspicion.telegram")

BASE = "https://api.telegram.org/bot{token}/"


async def call(token: str, method: str, params: dict | None = None,
               data: dict | None = None, files: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.post(
            BASE.format(token=token) + method,
            params=params, data=data, files=files,
        )
    response.raise_for_status()
    return response.json()


async def file_bytes(token: str, file_id: str) -> bytes:
    info = await call(token, "getFile", params={"file_id": file_id})
    path = info["result"]["file_path"]
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
    response.raise_for_status()
    return response.content


async def send_message(token: str, chat_id: int, text: str, keyboard: list | None = None,
                       plain: str | None = None) -> None:
    data = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML"}
    if keyboard:
        data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    try:
        await call(token, "sendMessage", data=data)
    except httpx.HTTPStatusError as exc:
        log.warning("HTML send failed (%s): %s", exc.response.status_code, exc.response.text[:300])
        if exc.response.status_code == 400 and plain is not None:
            fallback = {"chat_id": chat_id, "text": plain[:4000]}
            if keyboard:
                fallback["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
            await call(token, "sendMessage", data=fallback)
        else:
            raise


async def send_photo(token: str, chat_id: int, image: bytes, caption: str = "") -> None:
    await call(
        token, "sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1000]},
        files={"photo": ("share.png", image, "image/png")},
    )


async def answer_callback(token: str, callback_id: str, text: str) -> None:
    await call(token, "answerCallbackQuery", data={"callback_query_id": callback_id, "text": text})


GRACE_SECONDS = 900  # replay what arrived while asleep, ignore older backlog


def _update_date(update: dict) -> int:
    for key in ("message", "edited_message", "channel_post"):
        stamp = update.get(key, {}).get("date")
        if isinstance(stamp, int):
            return stamp
    callback = update.get("callback_query") or {}
    stamp = (callback.get("message") or {}).get("date")
    return stamp if isinstance(stamp, int) else 0


async def _newest_offset(token: str, save_offset=None) -> int:
    """Decide where to start when there is no stored offset.

    A free host can lose its database on restart, so a missing offset does not
    mean "brand new bot" — it can mean "we answered these an hour ago". Only
    updates newer than GRACE_SECONDS are replayed; anything older is dropped
    rather than re-answered.
    """
    try:
        result = await call(token, "getUpdates", params={"timeout": 0, "limit": 100})
        updates = result.get("result", [])
    except Exception:
        log.warning("could not read pending updates; starting at offset 0", exc_info=True)
        return 0
    if not updates:
        return 0

    cutoff = int(time.time()) - GRACE_SECONDS
    recent = [u for u in updates if _update_date(u) >= cutoff]
    offset = (recent[0]["update_id"] if recent else updates[-1]["update_id"] + 1)
    if save_offset:
        save_offset(offset)
    log.info(
        "no stored offset: %s pending update(s), replaying %s from update %s",
        len(updates), len(recent), offset,
    )
    return offset


async def poll(token: str, handler, offset: int | None = 0, save_offset=None) -> None:
    if offset is None:
        offset = await _newest_offset(token, save_offset)
    while True:
        try:
            result = await call(token, "getUpdates", params={"offset": offset, "timeout": 30})
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                if save_offset:
                    save_offset(offset)
                try:
                    await handler(update)
                except Exception:
                    log.exception("handler failed for update %s", update.get("update_id"))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll failed")
            await asyncio.sleep(3)
