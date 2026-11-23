"""PII stripping. Keep domains (needed for checks), drop identities."""

import hashlib
import re

from .config import HASH_SALT

# Indian mobile, with or without +91 / country separator.
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{9}(?!\d)")
# Email/UPI local part; domain deliberately kept for domain checks.
_LOCAL = re.compile(r"(?<![\w.\-])([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_AADHAAR = re.compile(r"(?<!\d)\d{12}(?!\d)")
_PAN = re.compile(r"(?<!\w)[A-Z]{5}\d{4}[A-Z](?!\w)")
_ACCOUNT = re.compile(r"(?:account|a/c)\s*(?:no\.?|number)?\s*[:\-]?\s*\d{9,18}", re.I)


def redact(text: str) -> str:
    text = _PHONE.sub("[PHONE]", text or "")
    text = _LOCAL.sub(lambda m: "redacted@" + m.group(2), text)
    text = _AADHAAR.sub("[ID]", text)
    text = _PAN.sub("[ID]", text)
    text = _ACCOUNT.sub("account [ID]", text)
    return text


def hash_id(value: str) -> str:
    return hashlib.sha256((HASH_SALT + "|" + (value or "")).encode()).hexdigest()[:16]


def evidence_quote(text: str, limit: int = 240) -> str:
    q = " ".join(redact(text).split())
    return q if len(q) <= limit else q[: limit - 1] + "…"
