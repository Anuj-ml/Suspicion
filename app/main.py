"""Suspicion MVP — FastAPI + Telegram bot."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from html import escape

from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

from . import media, sharecard, store, telegram
from .checks import NEXT_STEPS, TOP_STEPS, checks, evaluate, findings
from .config import BOT_HANDLE, PUBLIC_URL, TELEGRAM_TOKEN
from .extract import extract
from .redact import evidence_quote
from .scoring import band


class CheckIn(BaseModel):
    text: str
    country: str = "IN"
    online: bool = True


def run_check(text: str, country: str = "IN", online: bool = True) -> dict:
    offer = extract(text)
    signals = evaluate(text, offer, online=online, country=country)
    result = {
        "band": band(signals),
        "signals": [asdict(s) for s in signals],
        "offer": offer.as_dict(),
        "quote": evidence_quote(text),
        "findings": findings(signals, offer),
        "checks": checks(signals),
        "identity": (
            "registry ID given" if any(s.code == "registry_identifier" for s in signals)
            else "brand record found" if any(s.code == "company_record" for s in signals)
            else "not verified"
        ),
    }
    result["markdown"] = format_result(result)
    result["plain"] = format_plain(result)
    return result


def _h(value) -> str:
    return escape(str(value), quote=False)


def _a(text: str, url: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{_h(text)}</a>' if url else _h(text)


def format_result(result: dict) -> str:
    """Telegram HTML. No escaping noise, so formatting never falls back."""
    band = result["band"]
    found = result["findings"]
    lines = [
        f"<b>{_h(band.upper())} RISK</b>",
        f"{len(found)} issue{'s' if len(found) != 1 else ''} · identity {_h(result['identity'])}",
        "",
        "<b>WHAT STANDS OUT</b>",
    ]
    if not found:
        lines.append("Nothing in this message matched a known risk pattern.")
    for i, item in enumerate(found, 1):
        lines.append("")
        lines.append(f"<b>{i}. {_h(item['headline'])}</b>")
        if item["quote"]:
            lines.append(f"“{_h(item['quote'])}”")
        lines.append(f"→ {_h(item['action'])}")

    if result["checks"]:
        lines += ["", "<b>CHECKS RUN</b>"]
        for check in result["checks"]:
            source = _a("source", check["url"]) if check.get("url") else ""
            tail = f" ({source})" if source else ""
            lines.append(f"• {_h(check['label'])} — {_h(check['value'])}{tail}")

    lines += ["", "<b>DO NOW</b>"]
    for i, step in enumerate(TOP_STEPS.get(band, TOP_STEPS["Low"]), 1):
        lines.append(f"{i}. {_h(step)}")

    lines += ["", f"Risk signals, not a verdict. {_h(BOT_HANDLE)}"]
    return "\n".join(lines)


def format_plain(result: dict) -> str:
    band = result["band"]
    found = result["findings"]
    lines = [
        f"{band.upper()} RISK",
        f"{len(found)} issue{'s' if len(found) != 1 else ''} | identity {result['identity']}",
        "",
        "WHAT STANDS OUT",
    ]
    if not found:
        lines.append("Nothing in this message matched a known risk pattern.")
    for i, item in enumerate(found, 1):
        lines.append("")
        lines.append(f"{i}. {item['headline']}")
        if item["quote"]:
            lines.append(f'   "{item["quote"]}"')
        lines.append(f"   -> {item['action']}")
    if result["checks"]:
        lines += ["", "CHECKS RUN"]
        for check in result["checks"]:
            url = f" {check['url']}" if check.get("url") else ""
            lines.append(f"- {check['label']}: {check['value']}{url}")
    lines += ["", "DO NOW"]
    lines.extend(f"{i}. {step}" for i, step in enumerate(TOP_STEPS.get(band, TOP_STEPS["Low"]), 1))
    lines += ["", "Risk signals, not a verdict.", BOT_HANDLE]
    return "\n".join(lines)


async def handle_update(update: dict) -> None:
    callback = update.get("callback_query")
    if callback:
        data = callback.get("data", "")
        if data.startswith("legit:"):
            store.add_feedback(data.split(":", 1)[1])
            await telegram.answer_callback(TELEGRAM_TOKEN, callback["id"], "Logged. Thanks.")
        return

    message = update.get("message")
    if not message:
        return
    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    if text.startswith(("/start", "/help")):
        await telegram.send_message(
            TELEGRAM_TOKEN, chat_id,
            "Forward a recruiter message, offer PDF, or screenshot. "
            "I return risk signals + evidence, never a verdict.",
        )
        return
    document = message.get("document")
    if document and (document.get("mime_type") == "application/pdf"
                     or (document.get("file_name") or "").lower().endswith(".pdf")):
        try:
            text = media.pdf_text(await telegram.file_bytes(TELEGRAM_TOKEN, document["file_id"])) + "\n" + text
        except RuntimeError as exc:
            text += f"\n[{exc}]"
    photos = message.get("photo")
    if photos:
        try:
            text = media.image_text(await telegram.file_bytes(TELEGRAM_TOKEN, photos[-1]["file_id"])) + "\n" + text
        except RuntimeError as exc:
            text += f"\n[{exc}]"
    if not text.strip():
        await telegram.send_message(TELEGRAM_TOKEN, chat_id, "Forward a recruiter message, offer PDF, or screenshot.")
        return

    result = run_check(text)
    result["id"] = store.save(result["band"], result["signals"], result["quote"])
    keyboard = [[{"text": "This is legit", "callback_data": "legit:" + result["id"]}]]
    await telegram.send_message(
        TELEGRAM_TOKEN, chat_id, result["markdown"],
        keyboard=keyboard, plain=result["plain"],
    )
    image = sharecard.png(result)
    if image:
        await telegram.send_photo(TELEGRAM_TOKEN, chat_id, image, caption=f"{BOT_HANDLE} · {result['band']} risk")


async def keepalive(url: str) -> None:
    """Free hosts stop an instance after ~15 min without inbound traffic.

    A self-request to /health every 10 minutes counts as traffic, so polling
    never gets suspended mid-conversation.
    """
    import httpx

    target = url.rstrip("/") + "/health"
    while True:
        await asyncio.sleep(600)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                await client.get(target)
        except Exception:
            pass  # a failed ping is not worth a log line every 10 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    tasks = []
    if TELEGRAM_TOKEN:
        tasks.append(asyncio.create_task(telegram.poll(
            TELEGRAM_TOKEN, handle_update,
            offset=store.get_offset(), save_offset=store.set_offset,
        )))
    if PUBLIC_URL:
        tasks.append(asyncio.create_task(keepalive(PUBLIC_URL)))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Suspicion", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "product": "Suspicion"}


@app.post("/check")
def check(payload: CheckIn) -> dict:
    result = run_check(payload.text, payload.country, payload.online)
    result["id"] = store.save(result["band"], result["signals"], result["quote"])
    return result


@app.get("/verdict/{vid}")
def verdict(vid: str) -> dict:
    found = store.get(vid)
    if not found:
        return {"error": "not found"}
    return found


@app.post("/feedback/{vid}")
def feedback(vid: str) -> dict:
    store.add_feedback(vid)
    return {"ok": True}


@app.get("/share/{vid}")
def share(vid: str) -> Response:
    found = store.get(vid)
    if not found:
        return Response("not found", status_code=404)
    image = sharecard.png(found)
    if image:
        return Response(image, media_type="image/png")
    return Response(sharecard.svg(found), media_type="image/svg+xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)