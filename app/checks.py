"""Deterministic checks. Every signal carries its own evidence + source."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import lookup
from .extract import Offer
from .redact import redact

FREE_MAIL = {
    "gmail.com", "yahoo.com", "yahoo.co.in", "rediffmail.com", "hotmail.com",
    "outlook.com", "live.com", "icloud.com", "qq.com", "proton.me",
}

_FEE_RX = re.compile(
    r"(?:registration|processing|training|security|joining|admission|activation|laptop|placement|selection|refundable)\s*"
    r"(?:fee|charge|deposit|amount)|(?:pay|deposit|transfer|submit)\s*(?:₹|rs\.?|inr)?\s*\d+",
    re.I,
)
_CIN = re.compile(r"(?<!\w)[UDL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}(?!\w)")
_GSTIN = re.compile(r"(?<!\w)\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z(?!\w)")
_INTERVIEW = re.compile(r"no interview|without interview|direct (?:offer|joining|selection)", re.I)
_BAD_TERMS = re.compile(r"unpaid training|without pay|\bbond\b|deduct(?:ion)? from salary|no stipend", re.I)
_UPI_RX = re.compile(r"(?:pay|transfer|deposit|send|via)[^.!?\n]{0,70}\b(?:upi|gift card|crypto)\b[^.!?\n]{0,30}", re.I)
_DOC_RX = re.compile(r"(?:aadhaar|\bpan\b|passport|bank statement|google form|typeform|photo id)[^.!?\n]{0,40}", re.I)
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", re.I)
_EMAIL_CTX_RX = re.compile(
    r"(?:contact|email|reach|hr)\s*[:\-]?\s*[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    re.I,
)

NEXT_STEPS = {
    "fee_demand": "Don't pay. Ask for a written fee policy and CIN.",
    "payment_channel": "No personal UPI or gift-card payments. Use a verified company account after joining.",
    "data_request": "Hold Aadhaar/PAN until a signed offer. Verify who owns the form.",
    "free_mail": "Confirm this email on the official careers page; call the HR number listed there.",
    "lookalike_domain": "Compare the domain letter-by-letter with the official website.",
    "fresh_domain": "New domain + brand lookalike: do not reply.",
    "no_mx": "A sender domain that cannot receive mail is suspicious; verify by phone.",
    "mlm_pattern": "If pay comes from recruiting, not work, walk away.",
    "terms_review": "Get bond/pay/training terms in writing before signing.",
    "pressure": "Ask for 48 hours and a real interview.",
    "registry_identifier": "Open the registry link; match name, status, incorporation date.",
    "identity_unverified": "Ask for CIN/GSTIN and check the registry. No record is not proof of fraud.",
    "rdap_unavailable": "Check the domain age manually at the source link.",
    "domain_missing": "The domain in this message is not registered. Do not reply.",
    "no_dmarc": "Informational only; many legitimate domains lack DMARC.",
    "no_site": "A domain that serves no website cannot be the employer's real site.",
    "site_title_mismatch": "The homepage does not match the claimed company. Verify by phone.",
    "personal_vpa": "A personal UPI handle is not a company account. Do not transfer.",
    "offsite_form": "Do not submit ID documents to a form outside the employer's own domain.",
    "company_record": "Use the record to confirm the legal name and official site.",
    "company_unknown": "Ask for the registered legal name and CIN; this name has no public record.",
    "domain_mismatch": "The official site uses a different domain. Verify this email on that site.",
    "public_reports": "Open the report; check it names the same company or domain before you act.",
    "brand_impersonation": "A real brand is being copied. Check the offer only on its official site.",
    "no_reports": "No public reports found is not proof of legitimacy. Verify the employer directly.",
}

TOP_STEPS = {
    "High": [
        "Do not pay or share Aadhaar/PAN.",
        "Find this role on the employer's own careers page.",
        "Ask for the CIN and check it on MCA.",
    ],
    "Elevated": [
        "Verify the sender and the role before paying or sharing documents.",
        "Call the HR number listed on the company's own website.",
    ],
    "Low": [
        "No strong signals here. Still verify the sender before paying.",
        "Check the official careers page for this role and email.",
    ],
}

_DEEP_LINKS = {
    "IN": "https://www.mca.gov.in/content/mca/global/en/mca/master-data.html",
    "GB": "https://find-and-update.company-information.service.gov.uk/search?q={q}",
    "US": "https://www.sec.gov/cgi-bin/browse-edgar?company={q}&action=getcompany",
    "AU": "https://abr.business.gov.au/Search/Results?SearchText={q}",
    "SG": "https://data.gov.sg/datasets?query=ACRA",
    "CA": "https://ised-isde.canada.ca/cbr-rec/",
}


@dataclass
class Signal:
    code: str
    category: str          # a fee, b identity, c data, d mlm, e terms, other
    severity: str          # hard | heuristic | info
    title: str
    evidence: str
    source: str
    observed: str
    source_url: str = ""
    detail: str = ""


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _quote(text: str, pattern: re.Pattern | None = None, limit: int = 150,
           context: bool = False) -> str:
    """Line/sentence-aligned evidence. Never crosses a line break or dumps the message."""
    clean = redact(text)
    if not pattern:
        return _clip(" ".join(clean.split()), limit)

    for line in clean.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            match = pattern.search(sentence)
            if not match:
                continue
            if not context:
                return _clip(sentence.strip(), limit)
            start = max(0, match.start() - 20)
            space = sentence.rfind(" ", 0, start)
            start = space + 1 if space != -1 else 0
            end = min(len(sentence), match.end() + 25)
            space = sentence.find(" ", end)
            end = space if space != -1 else len(sentence)
            return _clip(sentence[start:end].strip(), limit)
    return _clip(" ".join(clean.split()), limit)


def _rdap(domain: str) -> tuple[str, int | None]:
    """('missing'|'found'|'error', age_days). 404 = not registered, not a failure."""
    try:
        req = Request(
            "https://rdap.org/domain/" + domain,
            headers={"User-Agent": "suspicion-mvp/0.1"},
        )
        with urlopen(req, timeout=6) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        return ("missing" if exc.code == 404 else "error"), None
    except Exception:
        return "error", None
    for event in payload.get("events", []):
        if event.get("eventAction") == "registration":
            try:
                stamp = event.get("eventDate", "").replace("Z", "+00:00")
                created = datetime.fromisoformat(stamp)
                return "found", (datetime.now(timezone.utc) - created).days
            except ValueError:
                return "found", None
    return "found", None


def _site(domain: str) -> tuple[int | None, str]:
    """Real homepage fetch. Returns (status, page title). None = does not load."""
    for scheme in ("https", "http"):
        for host in (domain, "www." + domain):
            try:
                req = Request(
                    f"{scheme}://{host}/",
                    headers={"User-Agent": "Mozilla/5.0 (compatible; suspicion-mvp/0.1)"},
                )
                with urlopen(req, timeout=6) as resp:
                    body = resp.read(60000).decode("utf-8", "replace")
                    title = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
                    return resp.status, " ".join(title.group(1).split())[:90] if title else ""
            except Exception:
                continue
    return None, ""


def _dns(domain: str, record_type: str) -> list[str] | None:
    """Real DNS answer via Google DoH. None = lookup failed, [] = no records."""
    try:
        url = f"https://dns.google/resolve?name={quote(domain)}&type={record_type}"
        req = Request(url, headers={"User-Agent": "suspicion-mvp/0.1"})
        with urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return [answer.get("data", "") for answer in payload.get("Answer", [])]
    except Exception:
        return None


def fee_signal(text: str, offer: Offer) -> Signal | None:
    if not offer.fees and not _FEE_RX.search(text):
        return None
    hard = offer.nip_listed is True and (offer.internship is True or bool(re.search(r"intern", text, re.I)))
    return Signal(
        code="fee_demand",
        category="a",
        severity="hard" if hard else "heuristic",
        title="Fee demanded on NIP-listed internship" if hard else "Registration/advance fee requested",
        evidence=_quote(text, _FEE_RX),
        source="AICTE NIP terms §5" if hard else "advance-fee pattern; no general legal ban",
        source_url="https://internship.aicte-india.org/terms-and-conditions" if hard else "",
        observed=date.today().isoformat(),
    )


def payment_signal(text: str, offer: Offer) -> Signal | None:
    risky = {"upi", "gift_card", "crypto"}
    if not risky.intersection(offer.payment_channels):
        return None
    return Signal(
        code="payment_channel",
        category="a",
        severity="heuristic",
        title="Payment requested via " + "/".join(sorted(risky.intersection(offer.payment_channels))),
        evidence=_quote(text, _UPI_RX),
        source="payment-channel pattern",
        observed=date.today().isoformat(),
    )


def vpa_signal(text: str) -> Signal | None:
    """Personal UPI handle (Google Pay/PhonePe/Paytm VPA) instead of a business account."""
    match = _VPA_RX.search(text)
    if not match:
        return None
    return Signal(
        code="personal_vpa",
        category="a",
        severity="heuristic",
        title="Payment goes to a personal UPI handle",
        evidence=match.group(0),
        source="VPA suffix is a consumer payment app, not a company account",
        source_url="https://www.npci.org.in/what-we-do/upi/upi-ecosystem",
        observed=date.today().isoformat(),
    )


def domain_signals(text: str, offer: Offer, online: bool) -> list[Signal]:
    out = []
    domain = (offer.sender_domain or (offer.domains[0] if offer.domains else "")).lower()
    if domain in FREE_MAIL:
        out.append(Signal(
            code="free_mail",
            category="b",
            severity="heuristic",
            title="Sender uses free webmail",
            evidence=_quote(text, _EMAIL_CTX_RX, limit=100, context=True),
            source="sender-domain check; third-party recruiters exist, so not conclusive",
            detail=f"free webmail ({domain}), not a company domain",
            observed=date.today().isoformat(),
        ))

    lookalike = False
    company = re.sub(r"[^a-z0-9]", "", (offer.company or "").lower())
    token = domain.split(".")[0]
    if company and token and len(company) > 4 and token not in company:
        lookalike = SequenceMatcher(None, company, token).ratio() >= 0.75
    if lookalike:
        out.append(Signal(
            code="lookalike_domain",
            category="b",
            severity="heuristic",
            title="Sender domain resembles claimed employer name",
            evidence=_quote(text, _EMAIL_RX, limit=100, context=True),
            source="string similarity vs claimed company; no authoritative company→domain map",
            detail="looks like the brand name but is not an official domain on record",
            observed=date.today().isoformat(),
        ))

    if online and domain and domain not in FREE_MAIL:
        state, age = _rdap(domain)
        rdap_url = f"https://rdap.org/domain/{domain}"
        if state == "missing":
            out.append(Signal(
                code="domain_missing",
                category="b",
                severity="heuristic",
                title="Domain is not registered",
                evidence=f"{domain}: no RDAP record",
                source="RDAP registry lookup",
                source_url=rdap_url,
                detail="not registered",
                observed=date.today().isoformat(),
            ))
        elif state == "error":
            out.append(Signal(
                code="rdap_unavailable",
                category="b",
                severity="info",
                title="Domain age could not be fetched",
                evidence=f"{domain}: RDAP lookup failed",
                source="RDAP lookup attempted",
                source_url=rdap_url,
                detail="lookup failed",
                observed=date.today().isoformat(),
            ))
        elif age is not None and age < 90:
            out.append(Signal(
                code="fresh_domain",
                category="b",
                severity="heuristic",
                title=f"Domain registered {age} days ago",
                evidence=f"{domain}: registration date {age} days ago",
                source="RDAP registration event",
                source_url=rdap_url,
                detail=f"registered {age} days ago",
                observed=date.today().isoformat(),
            ))

        mx = _dns(domain, "MX")
        if mx is not None and not mx:
            suspicious = lookalike or state == "missing" or (age is not None and age < 90)
            out.append(Signal(
                code="no_mx",
                category="b",
                severity="heuristic" if suspicious else "info",
                title="Sender domain has no mail servers",
                evidence=f"{domain}: no MX records",
                source="DNS MX lookup",
                source_url=f"https://dns.google/resolve?name={quote(domain)}&type=MX",
                detail="no MX records",
                observed=date.today().isoformat(),
            ))

        dmarc = _dns(f"_dmarc.{domain}", "TXT")
        if dmarc is not None and not any("v=DMARC1" in value for value in dmarc):
            out.append(Signal(
                code="no_dmarc",
                category="b",
                severity="info",
                title="No DMARC policy on sender domain",
                evidence=f"_dmarc.{domain}: no DMARC TXT record",
                source="DNS TXT lookup",
                source_url=f"https://dns.google/resolve?name={quote('_dmarc.' + domain)}&type=TXT",
                detail="none published",
                observed=date.today().isoformat(),
            ))

        status, page_title = _site(domain)
        site_url = f"https://{domain}/"
        if status is None:
            out.append(Signal(
                code="no_site",
                category="b",
                severity="heuristic" if (lookalike or (age is not None and age < 90)) else "info",
                title="Domain does not serve a website",
                evidence=f"{domain}: no HTTP response",
                source="direct HTTP fetch",
                source_url=site_url,
                detail="does not load",
                observed=date.today().isoformat(),
            ))
        elif company and len(company) > 4:
            words = [w for w in re.findall(r"[a-z]{4,}", company.lower())]
            haystack = page_title.lower()
            if words and not any(w in haystack for w in words):
                out.append(Signal(
                    code="site_title_mismatch",
                    category="b",
                    severity="info",
                    title="Homepage does not name the claimed company",
                    evidence=f"title: {page_title or '(empty)'}",
                    source="direct HTTP fetch",
                    source_url=site_url,
                    detail=f"page title: {page_title or '(none)'}",
                    observed=date.today().isoformat(),
                ))
    return out


def data_signal(text: str, offer: Offer) -> Signal | None:
    chat_only = "chat_only" in offer.payment_channels
    if not offer.doc_requests and not chat_only:
        return None
    return Signal(
        code="data_request",
        category="c",
        severity="heuristic",
        title="Aadhaar/PAN or form-based data capture requested",
        evidence=_quote(text, _DOC_RX, limit=100, context=True),
        source="document/URL pattern",
        observed=date.today().isoformat(),
    )


def form_signal(text: str) -> Signal | None:
    match = _FORM_HOSTS.search(text)
    if not match:
        return None
    host = match.group(1).lower()
    return Signal(
        code="offsite_form",
        category="c",
        severity="heuristic",
        title="Documents collected on a third-party form",
        evidence=host,
        source="form host is not the employer's own domain",
        detail=host,
        observed=date.today().isoformat(),
    )


def mlm_signal(text: str, offer: Offer) -> Signal | None:
    if offer.fees and offer.referral_income and offer.has_product is False:
        return Signal(
            code="mlm_pattern",
            category="d",
            severity="heuristic",
            title="Fee plus referral income with no product described",
            evidence=_quote(text),
            source="pattern rule: fee + referral + no product",
            observed=date.today().isoformat(),
        )
    return None


def terms_signal(text: str) -> Signal | None:
    if not _BAD_TERMS.search(text):
        return None
    return Signal(
        code="terms_review",
        category="e",
        severity="info",
        title="Terms need review (not evidence of fraud)",
        evidence=_quote(text, _BAD_TERMS),
        source="contract-review flag; never stated as a scam claim",
        observed=date.today().isoformat(),
    )


def pressure_signal(text: str, offer: Offer) -> Signal | None:
    if offer.urgency or _INTERVIEW.search(text):
        return Signal(
            code="pressure",
            category="other",
            severity="heuristic",
            title="Time pressure or offer without interview",
            evidence=_quote(text, _URGENCY_RX),
            source="process pattern",
            observed=date.today().isoformat(),
        )
    return None


_URGENCY_RX = re.compile(r"within\s*(?:24|48)\s*hours|today only|immediately|hurry|last date|limited seats|join now", re.I)
_VPA_RX = re.compile(
    r"\b[\w.\-]{2,}@(?:ybl|oksbi|okicici|okhdfcbank|okaxis|paytm|apl|axl|ibl|airtel|fbl|jupiteraxis)\b",
    re.I,
)
_FORM_HOSTS = re.compile(
    r"https?://(?:www\.)?(docs\.google\.com|forms\.gle|forms\.google\.com|typeform\.com|jotform\.com|surveymonkey\.com|airtable\.com)",
    re.I,
)
_AMOUNT_RX = re.compile(
    r"(?:₹|rs\.?|inr)\s?([\d][\d,]{2,})"
    r"|(?:fee|deposit|charge|amount)\s*(?:of|is)?\s*([\d][\d,]{2,})",
    re.I,
)


def registry_signals(offer: Offer, country: str) -> list[Signal]:
    base = _DEEP_LINKS.get(country.upper(), _DEEP_LINKS["IN"])
    q = offer.company or "company"
    link = base.replace("{q}", quote(q))
    ids = _CIN.findall(" ".join(offer.domains) + " " + offer.company) + _GSTIN.findall(offer.company)
    if ids:
        return [Signal(
            code="registry_identifier",
            category="b",
            severity="info",
            title="Registry ID present — verify at source",
            evidence=ids[0],
            source="official registry",
            source_url=link,
            detail=f"{ids[0]} found in message",
            observed=date.today().isoformat(),
        )]
    return [Signal(
        code="identity_unverified",
        category="b",
        severity="info",
        title="Company identity not verified",
        evidence="No CIN/GSTIN extracted. Absence is not evidence of fraud.",
        source="official registry",
        source_url=link,
        detail="none in message",
        observed=date.today().isoformat(),
    )]


def company_signals(offer: Offer) -> list[Signal]:
    """Public record + public fraud coverage. Links only, no verdict."""
    name = (offer.company or "").strip()
    domain = (offer.sender_domain or "").lower()
    if not name and not domain:
        return []
    out: list[Signal] = []
    who = name or domain.split(".")[0]
    today = date.today().isoformat()

    record = lookup.company_record(who)
    if record:
        hosts = {
            site.split("://")[-1].split("/")[0].lower().removeprefix("www.")
            for site in (record.get("websites") or [])
        }
        hosts.discard("")
        bits = [record["label"]]
        if record["description"]:
            bits.append(record["description"])
        if record["founded"]:
            bits.append(f"founded {record['founded']}")
        if hosts:
            primary = sorted(hosts, key=lambda h: (not h.endswith(".com"), len(h), h))[0]
            bits.append("official site " + ", ".join(sorted(
                hosts, key=lambda h: (not h.endswith(".com"), len(h), h))[:2]))
        out.append(Signal(
            code="company_record",
            category="b",
            severity="info",
            title="Company record found",
            evidence=record["label"],
            source="Wikidata",
            source_url=record["url"],
            detail=" · ".join(bits),
            observed=today,
        ))
        if hosts and domain and domain not in FREE_MAIL:
            if not any(domain == host or domain.endswith("." + host) for host in hosts):
                out.append(Signal(
                    code="domain_mismatch",
                    category="b",
                    severity="heuristic",
                    title="Sender domain is not the company's official site",
                    evidence=f"{domain} vs {primary}",
                    source="Wikidata official website (P856)",
                    source_url=record["url"],
                    detail=primary,
                    observed=today,
                ))
    else:
        out.append(Signal(
            code="company_unknown",
            category="b",
            severity="info",
            title="No public company record",
            evidence=f"no entity matched “{who}”",
            source="Wikidata search",
            source_url=lookup.wikidata_url(who),
            detail="none matched",
            observed=today,
        ))

    hits = lookup.fraud_coverage(who)
    search_url = lookup.news_url(who)
    if hits:
        impersonation = bool(record) and any(hit["impersonation"] for hit in hits)
        out.append(Signal(
            code="brand_impersonation" if impersonation else "public_reports",
            category="f",
            severity="heuristic",
            title="Scammers use this brand's name" if impersonation
            else "Public coverage mentions this name and fraud",
            evidence=hits[0]["title"][:140],
            source="Google News search results",
            source_url=hits[0]["url"],
            detail=f"{len(hits)} result(s) for “{who}” + fraud",
            observed=today,
        ))
    else:
        out.append(Signal(
            code="no_reports",
            category="b",
            severity="info",
            title="No fraud reports found in public news",
            evidence="nothing indexed for this name plus fraud wording",
            source="Google News search",
            source_url=search_url,
            detail="none found",
            observed=today,
        ))
    return out


def evaluate(text: str, offer: Offer, online: bool = False, country: str = "IN") -> list[Signal]:
    signals = []
    for fn in (fee_signal, payment_signal, vpa_signal, data_signal, form_signal, mlm_signal):
        signal = fn(text) if fn in (vpa_signal, form_signal) else fn(text, offer)
        if signal:
            signals.append(signal)
    signals.extend(domain_signals(text, offer, online))
    for signal in (terms_signal(text), pressure_signal(text, offer)):
        if signal:
            signals.append(signal)
    signals.extend(registry_signals(offer, country))
    if online:
        signals.extend(company_signals(offer))
    return signals


# --- presentation helpers ---------------------------------------------------
# One finding per issue. Five regex hits on the same sentence is slop, not proof.

GROUP_ORDER = ("a", "c", "b", "f", "d", "e", "other")
_DOC_ORDER = (
    ("aadhaar", "Aadhaar"),
    ("pan", "PAN"),
    ("passport", "passport"),
    ("bank statement", "bank statement"),
    ("driving licen", "driving licence"),
    ("photo id", "photo ID"),
)
_CHECK_LABELS = {
    "fresh_domain": "Domain age",
    "domain_missing": "Domain",
    "rdap_unavailable": "Domain age",
    "no_mx": "Mail servers",
    "no_dmarc": "Email policy",
    "no_site": "Website",
    "site_title_mismatch": "Website",
    "registry_identifier": "Company ID",
    "identity_unverified": "Company ID",
    "offsite_form": "Form host",
    "personal_vpa": "Payment handle",
    "company_record": "Company record",
    "company_unknown": "Company record",
    "domain_mismatch": "Official site",
    "public_reports": "Public reports",
    "brand_impersonation": "Public reports",
    "no_reports": "Public reports",
}


def _docs_label(evidence: str, offer: Offer) -> str:
    blob = (evidence + " " + " ".join(offer.doc_requests)).lower()
    names = [label for token, label in _DOC_ORDER
             if (re.search(r"\bpan\b", blob) if token == "pan" else token in blob)]
    label = " + ".join(names) or "Personal documents"
    return label + " via an online form" if "form" in blob else label + " requested"


def _headline(category: str, items: list[Signal], offer: Offer) -> str:
    codes = {s.code for s in items}
    prefer = ("fee_demand", "payment_channel", "data_request", "free_mail",
              "lookalike_domain", "mlm_pattern", "terms_review", "pressure")
    evidence = next((s.evidence for s in items if s.code in prefer), items[0].evidence)

    if category == "a":
        if "personal_vpa" in codes:
            return "Payment to a personal UPI handle"
        amount = _AMOUNT_RX.search(evidence)
        if not amount:
            return "Payment asked before hiring"
        digits = (amount.group(1) or amount.group(2) or "").replace(",", "")
        if not digits.isdigit():
            return "Payment asked before hiring"
        return f"₹{int(digits):,} asked before hiring"
    if category == "c":
        return _docs_label(evidence, offer)
    if category == "b":
        domain = offer.sender_domain
        if domain in FREE_MAIL:
            return f"Sender uses {domain}"
        return f"Sender domain {domain}" if domain else "Sender identity"
    if category == "f":
        if items[0].code == "brand_impersonation":
            return "Scammers use this brand's name"
        return "Public coverage links this name to fraud"
    if category == "d":
        return "Pay-to-join referral scheme"
    if category == "e":
        return "Contract terms worth reading"
    if offer.urgency:
        return "Deadline pressure to accept"
    return "No interview before the offer"


def findings(signals: list[Signal], offer: Offer, limit: int = 4) -> list[dict]:
    groups: dict[str, list[Signal]] = {}
    for signal in signals:
        if signal.severity == "info":
            continue
        key = signal.category if signal.category in GROUP_ORDER else "other"
        groups.setdefault(key, []).append(signal)

    order = [key for key in GROUP_ORDER if key in groups]
    order.sort(key=lambda key: 0 if any(s.severity == "hard" for s in groups[key]) else 1)
    out = []
    for key in order[:limit]:
        items = sorted(groups[key], key=lambda s: 0 if s.severity == "hard" else 1)
        quote = "" if key == "b" else next(
            (s.evidence for s in items if s.code not in {"offsite_form", "personal_vpa"}),
            items[0].evidence,
        )
        primary = next((s for code in (
            "domain_missing", "brand_impersonation", "public_reports", "personal_vpa",
            "fee_demand", "data_request", "domain_mismatch", "free_mail", "mlm_pattern",
            "lookalike_domain", "no_site", "pressure", "terms_review",
        ) for s in items if s.code == code), items[0])
        out.append({
            "category": key,
            "headline": _headline(key, items, offer),
            "quote": quote,
            "action": NEXT_STEPS.get(primary.code, "Verify this independently."),
            "severity": items[0].severity,
        })
    return out


def checks(signals: list[Signal]) -> list[dict]:
    """What was actually looked up, and what came back."""
    out, seen = [], set()
    for signal in signals:
        label = _CHECK_LABELS.get(signal.code)
        if not label or label in seen:
            continue
        seen.add(label)
        out.append({
            "label": label,
            "value": signal.detail or signal.evidence,
            "url": signal.source_url,
        })
    return out
