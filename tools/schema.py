"""Canonical schema for the portfolio news monitor.

Single source of truth for the entity file's columns and vocabularies, the
article record shape, the network allowlist, and every hard limit. The
collector, the validator, the tests and the docs all read from here, so a
limit is changed in exactly one place.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Entity file (data/entities.csv) — the accuracy-critical input.
# --------------------------------------------------------------------------
ENTITY_COLS = [
    "ticker",          # unique key, e.g. "2678 HK Equity"
    "name",            # canonical display name (what the analyst sees)
    "aliases",         # pipe-separated; every name the entity is known by
    "context_terms",   # pipe-separated; corroborating words for ambiguous names
    "negative_terms",  # pipe-separated; if present, the article is rejected
    "country",
    "industry",
    "ambiguous",       # yes|no — does the name need corroboration to count?
    "status",          # see STATUSES
    "note",            # analyst-facing note, e.g. "acquired by Thiess, Oct 2022"
    "verified",        # ISO date the corporate status was last checked, or ""
]

ENTITY_KEY = "ticker"

STATUSES = {
    "active",      # trading under its own name
    "renamed",     # same issuer, new name — track the new one, keep old aliases
    "acquired",    # rolled into a successor — track the successor
    "sanctioned",  # still tracked; may be uninvestable
    "delisted",    # no longer listed; still tracked for residual news
}

YES_NO = {"yes", "no"}

# Fields that must never be blank.
ENTITY_REQUIRED = ["ticker", "name", "aliases", "country", "ambiguous", "status"]

# --------------------------------------------------------------------------
# Article record (docs/data/articles.json).
# --------------------------------------------------------------------------
ARTICLE_COLS = [
    "id",          # sha256(ticker + canonical_url)[:16] — stable dedupe key
    "ticker",
    "company",     # denormalised for the frontend; avoids a client-side join
    "headline",
    "url",
    "published",   # ISO-8601 UTC, e.g. "2026-08-24T06:12:00Z"
    "source",      # "gdelt" | "gnews"
    "publisher",   # outlet name where the feed gives one, else ""
    "confidence",  # "high" | "low"
]

CONFIDENCES = ("high", "low")
SOURCES = ("gdelt", "gnews")

# --------------------------------------------------------------------------
# Archive policy.
# --------------------------------------------------------------------------
# Rolling working set. Oldest are evicted as newer arrive, EXCEPT that each
# ticker keeps its most recent PER_TICKER_FLOOR records so a high-volume name
# cannot crowd a quiet one out of the archive entirely.
MAX_ARCHIVE_RECORDS = 3_000
PER_TICKER_FLOOR = 10

# Nothing before this date is collected (project start).
COLLECT_FROM = "2026-07-01"

# Per-company-per-run ceiling: bounds noise at the source.
MAX_PER_TICKER_PER_RUN = 25

# --------------------------------------------------------------------------
# Network — SSRF allowlist. A host not in this set is never contacted, and a
# redirect that leaves the set is refused rather than followed.
# --------------------------------------------------------------------------
ALLOWED_HOSTS = frozenset({
    "api.gdeltproject.org",
    "news.google.com",
})

# Schemes permitted in any URL we store or emit. Guards the frontend against
# javascript: / data: payloads arriving inside a feed.
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

HTTP_TIMEOUT_SECONDS = 20
HTTP_MAX_REDIRECTS = 3
HTTP_MAX_BYTES = 8 * 1024 * 1024      # response size cap
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 2.0
HTTP_MIN_INTERVAL_SECONDS = 1.0       # politeness floor between requests
USER_AGENT = "portfolio-news-monitor/1.0 (research; contact via repo)"

# --------------------------------------------------------------------------
# Parsing limits (defence against hostile or malformed feeds).
# --------------------------------------------------------------------------
MAX_ITEMS_PER_FEED = 250
MAX_HEADLINE_CHARS = 400
MAX_URL_CHARS = 2_048
