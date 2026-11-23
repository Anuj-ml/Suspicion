"""External lookups: public fraud coverage + company record.

Every result keeps its source URL. No scraping of search engines that fight
back (Bing/DDG return generic pages to bots), only feeds with stable formats.
"""

import json
import re
import time
from difflib import SequenceMatcher
from html import unescape
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
FRAUD = re.compile(r"\b(scam|scams|fraud|fraudulent|fake|cheat|cheating|complaint|caution|warning)\b", re.I)
DENIAL = re.compile(
    r"\b(denies|denied|refutes|refuted|clears|cleared|acquitted|allegations against|"
    r"victim|targeted by|sues|defamation)\b",
    re.I,
)
IMPERSONATION = re.compile(
    r"\b(using (?:the )?name|in the name of|impersonat\w*|posing as|fake (?:interview|offer|hr|recruit\w*)|"
    r"scam (?:text|call|message|mail)s?)\b",
    re.I,
)
TTL = 6 * 3600
_CACHE: dict[tuple[str, str], tuple[float, object]] = {}


def _get(url: str, timeout: int = 6) -> str:
    request = Request(url, headers=UA)
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _cached(kind: str, key: str, fn):
    cached = _CACHE.get((kind, key))
    if cached and cached[0] > time.time():
        return cached[1]
    value = fn()
    _CACHE[(kind, key)] = (time.time() + TTL, value)
    return value


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def news_url(name: str) -> str:
    """Human-clickable search for the reports we summarise."""
    return "https://www.google.com/search?q=" + quote(f'"{name}" scam OR fraud job')


def wikidata_url(name: str) -> str:
    return "https://www.wikidata.org/w/index.php?search=" + quote(name)


def company_record(name: str) -> dict | None:
    """Best-matching Wikidata entity: label, description, official site, founded."""
    if len(_norm(name)) < 4:
        return None
    return _cached("company", name.lower(), lambda: _company_record(name))


def _company_record(name: str) -> dict | None:
    try:
        payload = json.loads(_get(
            "https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json"
            "&language=en&limit=5&search=" + quote(name)
        ))
    except Exception:
        return None

    target = _norm(name)
    best = None
    for hit in payload.get("search", []):
        label = hit.get("label", "")
        score = SequenceMatcher(None, target, _norm(label)).ratio()
        if target in _norm(label) or _norm(label) in target:
            score = max(score, 0.95)
        if score >= 0.85 and (best is None or score > best[0]):
            best = (score, hit)
    if not best:
        return None

    hit = best[1]
    record = {
        "label": hit.get("label", ""),
        "description": hit.get("description", ""),
        "url": f"https://www.wikidata.org/wiki/{hit['id']}",
        "website": "",
        "founded": "",
    }
    try:
        entity = json.loads(_get(
            f"https://www.wikidata.org/wiki/Special:EntityData/{hit['id']}.json"
        ))["entities"][hit["id"]]
        claims = entity.get("claims", {})

        def first(prop: str, path: tuple[str, ...]) -> str:
            for claim in claims.get(prop, []):
                node = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
                for step in path:
                    if not isinstance(node, dict):
                        node = None
                        break
                    node = node.get(step)
                if isinstance(node, str) and node:
                    return node
            return ""

        record["website"] = first("P856", ())
        record["websites"] = []
        for claim in claims.get("P856", []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(value, str) and value:
                record["websites"].append(value)
        stamp = first("P571", ("time",))
        record["founded"] = stamp.lstrip("+")[:4] if stamp else ""
    except Exception:
        pass
    return record


def fraud_coverage(name: str) -> list[dict]:
    """News/posts indexed with this name plus fraud wording."""
    if len(_norm(name)) < 4:
        return []
    return _cached("news", name.lower(), lambda: _fraud_coverage(name))


def _fraud_coverage(name: str) -> list[dict]:
    query = f'"{name}" (scam OR fraud) (job OR recruitment OR hiring OR offer OR internship)'
    try:
        feed = _get(
            "https://news.google.com/rss/search?q=" + quote(query)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )
    except Exception:
        return []

    hits = []
    for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
        title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
        link = re.search(r"<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", item, re.S)
        if not title:
            continue
        clean = " ".join(unescape(re.sub(r"<[^>]+>", "", title.group(1))).split())
        if _accuses(clean, name):
            hits.append({
                "title": clean,
                "url": link.group(1).strip() if link else news_url(name),
                "impersonation": bool(IMPERSONATION.search(clean)),
            })
        if len(hits) >= 3:
            break
    return hits


def _accuses(title: str, name: str) -> bool:
    """Fraud word must sit next to the company name. Stops guilt-by-mention."""
    if DENIAL.search(title):
        return False
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 4]
    low = title.lower()
    for match in FRAUD.finditer(low):
        window = low[max(0, match.start() - 30):match.end() + 30]
        if any(re.search(rf"\b{re.escape(word)}", window) for word in words):
            return True
    return False
