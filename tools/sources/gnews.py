"""Google News RSS search.

Complements GDELT: it surfaces stories GDELT's crawl misses, and it is fast
and free. The trade-off is that its reach is recent-only and its links are
news.google.com redirectors rather than publisher URLs — which is fine here,
since links are handed to the analyst's browser, never followed by the tool.

The XML is parsed with defusedxml. An RSS feed is untrusted input, and the
stdlib XML parsers are vulnerable to entity-expansion and external-entity
attacks; defusedxml closes both.
"""
from __future__ import annotations

import datetime as dt
import email.utils
from urllib.parse import urlencode

from defusedxml import ElementTree as DefusedET

from .. import net, schema

ENDPOINT = "https://news.google.com/rss/search"

MAX_ALIASES_PER_QUERY = 4
MAX_SCOPE_TERMS = 6


def _or_block(terms: list[str]) -> str:
    block = " OR ".join(f'"{t}"' for t in terms)
    return f"({block})" if len(terms) > 1 else block


def build_queries(entity) -> list[str]:
    """One query per chunk of aliases, each scoped by required_terms.

    Google News treats a space as AND, so the scope block narrows the search
    at the source rather than after download.
    """
    # Strong aliases are self-scoping — "AWPLR" needs no "Sri Lanka" beside it —
    # so they are chunked separately and queried unscoped. Only the weak,
    # generic terms carry the scope block. Scoping the strong ones too would
    # silently drop coverage that is already unambiguous.
    strong = sorted((a for a in entity.aliases if a not in entity.weak),
                    key=len, reverse=True)
    weak = sorted((a for a in entity.aliases if a in entity.weak),
                  key=len, reverse=True)

    # Scope terms are taken in the order the resolver lists them, NOT sorted.
    # Sorting by length once picked the longest terms - which were outlet names
    # - and silently dropped "Sri Lanka" itself from the query.
    #
    # Weak aliases are generic ("inflation", "Peabody"), so they are always
    # narrowed at the source: by required_terms on a macro row, or by
    # context_terms on a company. Querying them bare returns the whole world
    # and leaves the matcher to throw almost all of it away.
    narrowing = entity.required_terms or entity.context_terms
    scope = " " + _or_block(narrowing[:MAX_SCOPE_TERMS]) if narrowing else ""

    def _chunk(terms):
        return [terms[i:i + MAX_ALIASES_PER_QUERY] for i in range(0, len(terms), MAX_ALIASES_PER_QUERY)]

    pairs = ([(c, "") for c in _chunk(strong)]
             + [(c, scope) for c in _chunk(weak)])
    pairs = pairs[:schema.MAX_QUERY_CHUNKS]

    return [_or_block(chunk) + suffix for chunk, suffix in pairs]


def build_query(entity) -> str:
    """First query only. Retained for callers that want a single expression."""
    return build_queries(entity)[0]


def _parse_pubdate(value: str) -> str | None:
    """RFC 2822 -> ISO-8601 UTC."""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_publisher(title: str, fallback: str) -> tuple[str, str]:
    """Google appends ' - Publisher' to each title; separate the two.

    Only the final dash-separated segment is treated as a publisher, and only
    when it is short and does not itself look like a sentence — otherwise a
    headline containing a dash would lose its ending.
    """
    if fallback:
        suffix = f" - {fallback}"
        if title.endswith(suffix):
            return title[: -len(suffix)].strip(), fallback
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        if head and 0 < len(tail) <= 60 and tail.count(" ") <= 6:
            return head.strip(), tail.strip()
    return title.strip(), fallback


def fetch_articles(entity, fetcher=net.fetch) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for query in build_queries(entity):
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        body = fetcher(f"{ENDPOINT}?{urlencode(params)}", accept="application/rss+xml")
        for item in parse(body):
            if item["url"] not in seen:
                seen.add(item["url"])
                out.append(item)
    return out


def parse(body: bytes) -> list[dict]:
    try:
        root = DefusedET.fromstring(body)
    except Exception:
        # Malformed or hostile XML: yield nothing rather than abort the run.
        return []

    out: list[dict] = []
    for item in root.iter("item"):
        if len(out) >= schema.MAX_ITEMS_PER_FEED:
            break
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = _parse_pubdate((item.findtext("pubDate") or "").strip())
        if not (title and link and published):
            continue
        source_el = item.find("source")
        fallback = (source_el.text or "").strip() if source_el is not None else ""
        headline, publisher = _split_publisher(title, fallback)
        if not headline:
            continue
        out.append({
            "headline": headline[:schema.MAX_HEADLINE_CHARS],
            "url": link,
            "published": published,
            "publisher": publisher[:120],
        })
    return out
