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


def build_query(entity, *, english_only: bool = False) -> str:
    """Quoted-phrase OR block, e.g. ("Tokyo Cement" OR "TKYO")."""
    aliases = sorted(entity.aliases, key=len, reverse=True)[:MAX_ALIASES_PER_QUERY]
    block = " OR ".join(f'"{a}"' for a in aliases)
    query = f"({block})" if len(aliases) > 1 else block
    if english_only:
        query += " sourcelang:english"
    return query


def _parse_seendate(value: str) -> str | None:
    """GDELT stamps are 'YYYYMMDDTHHMMSSZ'. Return ISO-8601 UTC."""
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_articles(entity, start: dt.date, end: dt.date,
                   fetcher=net.fetch) -> list[dict]:
    """Return raw {headline, url, published, publisher} dicts for one entity."""
    params = {
        "query": build_query(entity),
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(schema.MAX_ITEMS_PER_FEED),
        "sort": "DateDesc",
        "startdatetime": start.strftime("%Y%m%d") + "000000",
        "enddatetime": end.strftime("%Y%m%d") + "235959",
    }
    body = fetcher(f"{ENDPOINT}?{urlencode(params)}", accept="application/json")
    return parse(body)


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
