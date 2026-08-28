"""GDELT DOC 2.0 article search.

GDELT indexes worldwide news and exposes a free JSON search endpoint. It is
the primary source here because it reaches non-Anglophone regional press,
which matters for a portfolio weighted to Indonesia, Vietnam, Thailand and
Turkey.

Only the ArtList mode is used, which returns metadata — headline, link, seen
date, domain. Nothing fetches the article itself.
"""
from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlencode

from .. import net, schema

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT rejects a query whose OR-block grows too large, and an overlong query
# degrades into noise. Aliases beyond this are dropped, longest-first kept,
# because longer names are the more distinctive ones.
MAX_ALIASES_PER_QUERY = 6

# Scope terms are ANDed in alongside; keep the block short enough that the
# combined expression stays within what the endpoint accepts.
MAX_SCOPE_TERMS = 8


def _or_block(terms: list[str]) -> str:
    block = " OR ".join(f'"{t}"' for t in terms)
    return f"({block})" if len(terms) > 1 else block


def build_queries(entity, *, english_only: bool = False) -> list[str]:
    """One query per chunk of aliases, each scoped by required_terms.

    A company row has a handful of aliases and yields a single query. A macro
    row carries dozens of terms, so they are split across several queries -
    GDELT rejects an over-long OR block, and a bloated one returns noise.

    Where the row is scoped, the scope block is ANDed in at the source
    (juxtaposition means AND in GDELT's syntax) rather than only filtered
    afterwards. That is the difference between asking for "Sri Lankan
    inflation news" and downloading the world's inflation news to discard it.
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

    out = []
    for chunk, suffix in pairs:
        query = _or_block(chunk) + suffix
        if english_only:
            query += " sourcelang:english"
        out.append(query)
    return out


def build_query(entity, *, english_only: bool = False) -> str:
    """First query only. Retained for callers that want a single expression."""
    return build_queries(entity, english_only=english_only)[0]


def _parse_seendate(value: str) -> str | None:
    """GDELT stamps are 'YYYYMMDDTHHMMSSZ'. Return ISO-8601 UTC."""
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_articles(entity, start: dt.date, end: dt.date,
                   fetcher=net.fetch) -> list[dict]:
    """Return raw {headline, url, published, publisher} dicts for one entity.

    Results across query chunks are deduplicated by URL here, so a headline
    matching two of a macro row's terms is fetched once, not twice.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for query in build_queries(entity):
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(schema.MAX_ITEMS_PER_FEED),
            "sort": "DateDesc",
            "startdatetime": start.strftime("%Y%m%d") + "000000",
            "enddatetime": end.strftime("%Y%m%d") + "235959",
        }
        body = fetcher(f"{ENDPOINT}?{urlencode(params)}", accept="application/json")
        for item in parse(body):
            if item["url"] not in seen:
                seen.add(item["url"])
                out.append(item)
    return out


def parse(body: bytes) -> list[dict]:
    """Parse a GDELT ArtList response defensively.

    GDELT occasionally returns an HTML error page or a truncated body with a
    200 status, so a parse failure is treated as an empty result rather than
    an exception that would abort the whole run.
    """
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    out: list[dict] = []
    for item in (payload.get("articles") or [])[:schema.MAX_ITEMS_PER_FEED]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        published = _parse_seendate(str(item.get("seendate") or ""))
        if not (url and title and published):
            continue
        out.append({
            "headline": title[:schema.MAX_HEADLINE_CHARS],
            "url": url,
            "published": published,
            "publisher": str(item.get("domain") or "").strip()[:120],
        })
    return out
