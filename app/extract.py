"""Structured extraction only. This module NEVER decides risk."""

import json
import re
from dataclasses import asdict, dataclass, field

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from .redact import redact

_EMAIL_DOMAIN = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_URL_DOMAIN = re.compile(r"https?://(?:www\.)?([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_COMPANY = re.compile(r"(?:company|organisation|organization|employer)\s*[:\-]\s*([A-Za-z0-9 .&']{2,60})", re.I)
_FEE = re.compile(
    r"(?:registration|processing|training|security|joining|admission|activation|laptop|placement|selection|refundable)\s*"
    r"(?:fee|charge|deposit|amount)|(?:pay|deposit|transfer|submit)\s*(?:₹|rs\.?|inr)?\s*\d+",
    re.I,
)
_CHANNELS = [
    ("upi", re.compile(r"\bupi\b|\bupi id\b|@(?:ybl|oksbi|okhdfcbank|paytm|apl|axl|ibl)\b", re.I)),
    ("gift_card", re.compile(r"gift card|amazon pay|flipkart gift", re.I)),
    ("crypto", re.compile(r"\busdt\b|\bbtc\b|crypto wallet", re.I)),
    ("bank_transfer", re.compile(r"bank transfer|neft|imps|rtgs", re.I)),
    ("chat_only", re.compile(r"wa\.me/|t\.me/|whatsapp|telegram", re.I)),
]
_DOC_REQUEST = re.compile(
    r"aadhaar|\bpan\b|passport|bank statement|photo id|driving licen[cs]e|google form|typeform|form\.google",
    re.I,
)
_URGENCY = re.compile(r"within\s*(?:24|48)\s*hours|today only|immediately|hurry|last date|limited seats|join now", re.I)

# Groq strict structured outputs: all fields required, no extra properties.
OFFER_SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": "string"},
        "sender_domain": {"type": "string"},
        "domains": {"type": "array", "items": {"type": "string"}},
        "fees": {"type": "array", "items": {"type": "string"}},
        "payment_channels": {"type": "array", "items": {"type": "string"}},
        "internship": {"type": ["boolean", "null"]},
        "nip_listed": {"type": ["boolean", "null"]},
        "doc_requests": {"type": "array", "items": {"type": "string"}},
        "urgency": {"type": "boolean"},
        "referral_income": {"type": "boolean"},
        "has_product": {"type": ["boolean", "null"]},
        "salary": {"type": "string"},
    },
    "required": [
        "company", "sender_domain", "domains", "fees", "payment_channels",
        "internship", "nip_listed", "doc_requests", "urgency",
        "referral_income", "has_product", "salary",
    ],
    "additionalProperties": False,
}
_REFERRAL = re.compile(r"refer(?:ral)?|downline|team income|earn per (?:person|friend)|multi[- ]level", re.I)
_SALARY = re.compile(r"(?:stipend|salary|ctc|pay)\s*[:\-]?\s*(₹|rs\.?|inr)?\s*\d[\d,]*", re.I)
_NIP = re.compile(r"internship\.aicte-india\.org|national internship portal|\baicte\b", re.I)
_INTERNSHIP = re.compile(r"\bintern(?:ship)?\b", re.I)


@dataclass
class Offer:
    company: str = ""
    sender_domain: str = ""
    domains: list = field(default_factory=list)
    fees: list = field(default_factory=list)
    payment_channels: list = field(default_factory=list)
    internship: bool | None = None
    nip_listed: bool | None = None
    doc_requests: list = field(default_factory=list)
    urgency: bool = False
    referral_income: bool = False
    has_product: bool | None = None
    salary: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _regex_extract(text: str) -> Offer:
    offer = Offer()
    domains = []
    m = _EMAIL_DOMAIN.search(text)
    if m:
        offer.sender_domain = m.group(1).lower()
        domains.append(offer.sender_domain)
    domains.extend(d.lower() for d in _URL_DOMAIN.findall(text))
    offer.domains = list(dict.fromkeys(domains))

    m = _COMPANY.search(text)
    if m:
        offer.company = m.group(1).strip()
    offer.fees = [m.group(0) for m in _FEE.finditer(text)][:5]
    offer.payment_channels = [name for name, rx in _CHANNELS if rx.search(text)]
    offer.doc_requests = [m.group(0) for m in _DOC_REQUEST.finditer(text)][:5]
    offer.urgency = bool(_URGENCY.search(text))
    offer.referral_income = bool(_REFERRAL.search(text))
    offer.nip_listed = bool(_NIP.search(text)) or None
    offer.internship = bool(_INTERNSHIP.search(text)) or None
    if re.search(r"no product|without any product|no service", text, re.I):
        offer.has_product = False
    m = _SALARY.search(text)
    if m:
        offer.salary = m.group(0).strip()
    return offer


def _llm_extract(clean: str) -> dict | None:
    import httpx  # lazy: bot works without LLM dependency configured

    system = (
        "Extract structured fields from a job or internship message. "
        "Return JSON only. Never judge legitimacy. Keys: company, sender_domain, "
        "domains (list), fees (list of quoted phrases), payment_channels (list of "
        "upi|gift_card|crypto|bank_transfer|chat_only), internship (bool), "
        "nip_listed (bool or null), doc_requests (list), urgency (bool), "
        "referral_income (bool), has_product (bool or null), salary (string)."
    )
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": clean},
        ],
    }
    formats = [
        {"type": "json_schema", "json_schema": {
            "name": "offer_extraction", "strict": True, "schema": OFFER_SCHEMA,
        }},
        {"type": "json_object"},  # fallback for models without strict mode
    ]
    for response_format in formats:
        try:
            response = httpx.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                json={**payload, "response_format": response_format},
                timeout=20,
            )
            response.raise_for_status()
            return json.loads(response.json()["choices"][0]["message"]["content"])
        except Exception:
            continue
    return None


def _merge(base: Offer, remote: dict) -> Offer:
    for key in ("company", "sender_domain", "salary"):
        if isinstance(remote.get(key), str) and remote[key].strip():
            value = remote[key].strip()[:120]
            if key == "sender_domain" and "." not in value:
                continue  # a UPI suffix like 'okaxis' is not a domain
            setattr(base, key, value)
    for key in ("domains", "fees", "payment_channels", "doc_requests"):
        value = remote.get(key)
        if isinstance(value, list):
            setattr(base, key, [str(v)[:120] for v in value if v][:10])
    for key in ("urgency", "referral_income"):
        if isinstance(remote.get(key), bool):
            setattr(base, key, remote[key])
    for key in ("internship", "nip_listed", "has_product"):
        if isinstance(remote.get(key), bool):
            setattr(base, key, remote[key])
    return base


def extract(text: str) -> Offer:
    """Regex baseline; LLM enrichment when configured. Both see redacted text."""
    clean = redact(text)
    offer = _regex_extract(clean)
    if LLM_API_KEY:
        try:
            remote = _llm_extract(clean)
            if remote:
                offer = _merge(offer, remote)
        except Exception:
            pass  # LLM is best-effort; deterministic checks still run
    return offer
