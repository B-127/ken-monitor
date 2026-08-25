"""The article archive: a rolling working set, not a permanent record.

Policy (see schema.py for the numbers):

  * The archive holds at most MAX_ARCHIVE_RECORDS articles. When newer ones
    arrive, older ones are evicted.
  * Eviction is oldest-first, but each ticker retains its most recent
    PER_TICKER_FLOOR articles before the global cut applies. Without that
    floor, a heavily-covered name would evict every article about a quiet
    one, and the quiet names are exactly where an analyst needs the help.
  * A record is one (ticker, article) pair. The same story matching two
    companies is two records, so filtering by company stays a simple filter.

Writes are atomic — a timestamped copy of the previous file, then temp-write
and replace — so an interrupted run cannot leave a truncated archive in the
working tree ready to be committed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile

from . import matching, net, schema


class ArchiveError(Exception):
    pass


class Lock:
    """Best-effort exclusive lock via O_CREAT|O_EXCL."""

    def __init__(self, target: str):
        self.path = target + ".lock"
        self.fd: int | None = None

    def __enter__(self) -> "Lock":
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError as exc:
            raise ArchiveError(
                "archive is locked by another run; remove the .lock file if stale."
            ) from exc
        return self

    def __exit__(self, *exc) -> None:
        if self.fd is not None:
            os.close(self.fd)
        if os.path.exists(self.path):
            os.remove(self.path)


def record_id(ticker: str, canonical: str) -> str:
    digest = hashlib.sha256(f"{ticker}\x00{canonical}".encode("utf-8"))
    return digest.hexdigest()[:16]


def make_record(entity, raw: dict, source: str, confidence: str) -> dict | None:
    """Build a validated archive record, or None if the input is unusable."""
    url = (raw.get("url") or "").strip()
    if not net.is_safe_public_url(url):
        return None
    headline = (raw.get("headline") or "").strip()[:schema.MAX_HEADLINE_CHARS]
    published = (raw.get("published") or "").strip()
    if not headline or not published:
        return None
    if source not in schema.SOURCES or confidence not in schema.CONFIDENCES:
        return None
    canonical = matching.canonical_url(url)
    return {
        "id": record_id(entity.ticker, canonical),
        "ticker": entity.ticker,
        "company": entity.name,
        "headline": headline,
        "url": url,
        "published": published,
        "source": source,
        "publisher": (raw.get("publisher") or "").strip()[:120],
        "confidence": confidence,
        "kind": entity.kind,
    }


def signature(records: list[dict]) -> str:
    """Stable fingerprint of an article set, ignoring ordering.

    The archive carries a `generated` timestamp that changes on every run, so
    a plain file diff is always dirty and CI would commit daily whether or not
    anything was found. Comparing signatures instead means the repository only
    gains a commit when the headlines actually changed.
    """
    joined = "\x00".join(sorted(r.get("id", "") for r in records))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def load(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArchiveError(f"existing archive is not valid JSON: {exc}") from exc
    articles = payload.get("articles") if isinstance(payload, dict) else payload
    if not isinstance(articles, list):
        raise ArchiveError("archive JSON has no 'articles' list.")
    return [a for a in articles if isinstance(a, dict) and a.get("id")]


def merge(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int]:
    """Union by record id, then drop syndicated near-duplicates.

    Existing records win over incoming ones with the same id so an archived
    headline is never silently rewritten by a later feed rendering of it.
    """
    by_id: dict[str, dict] = {}
    for rec in existing:
        by_id[rec["id"]] = rec

    added = 0
    for rec in incoming:
        if rec["id"] not in by_id:
            by_id[rec["id"]] = rec
            added += 1

    # Same company + same normalised title from a different outlet is one story.
    seen_story: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for rec in sorted(by_id.values(), key=lambda r: r.get("published", ""), reverse=True):
        key = (rec["ticker"], matching.dedupe_title(rec.get("headline", "")))
        if key in seen_story:
            continue
        seen_story.add(key)
        deduped.append(rec)
    return deduped, added


def apply_window(records: list[dict],
                 cap: int = schema.MAX_ARCHIVE_RECORDS,
                 floor: int = schema.PER_TICKER_FLOOR,
                 macro_share: float = schema.MACRO_SHARE_OF_ARCHIVE) -> list[dict]:
    """Trim to `cap` under two protections.

    Each row keeps its newest `floor` records, so a heavily-covered name cannot
    evict a quiet one entirely. Beyond the floors, macro records are held to
    `macro_share` of the cap: six economic themes covering ~170 terms otherwise
    out-produce all 74 companies put together and would push the equities the
    portfolio actually holds out of the window.
    """
    ordered = sorted(records, key=lambda r: r.get("published", ""), reverse=True)
    if len(ordered) <= cap:
        return ordered

    protected: list[dict] = []
    remainder: list[dict] = []
    per_ticker: dict[str, int] = {}
    for rec in ordered:
        ticker = rec.get("ticker", "")
        count = per_ticker.get(ticker, 0)
        if count < floor:
            per_ticker[ticker] = count + 1
            protected.append(rec)
        else:
            remainder.append(rec)

    # If the floors alone exceed the cap, keep the newest of them rather than
    # over-filling — the cap is the hard promise.
    if len(protected) >= cap:
        return protected[:cap]

    slots = cap - len(protected)
    macro_budget = max(0, int(cap * macro_share) -
                       sum(1 for r in protected if r.get("kind") == "macro"))

    filler: list[dict] = []
    for rec in remainder:
        if len(filler) >= slots:
            break
        if rec.get("kind") == "macro":
            if macro_budget <= 0:
                continue
            macro_budget -= 1
        filler.append(rec)

    # If company volume could not fill the window, let macro take the slack
    # rather than shipping a half-empty archive.
    if len(filler) < slots:
        chosen = {id(r) for r in filler}
        for rec in remainder:
            if len(filler) >= slots:
                break
            if id(rec) not in chosen:
                filler.append(rec)

    return sorted(protected + filler,
                  key=lambda r: r.get("published", ""), reverse=True)


def entity_manifest(entities) -> list[dict]:
    """Compact company list for the dashboard rail.

    Every tracked company is listed, including ones with no articles, so a
    silent name reads as 'nothing this week' rather than vanishing from the
    interface and looking like it was dropped from the portfolio.
    """
    return [{
        "ticker": e.ticker,
        "name": e.name,
        "country": e.country,
        "industry": e.industry,
        "status": e.status,
        "note": e.note,
        "kind": e.kind,
    } for e in entities]


def save(path: str, records: list[dict], *, entities: list[dict],
         backup_dir: str | None = None) -> None:
    """Atomically write the archive, backing up any previous version first."""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if backup_dir and os.path.isfile(path):
        os.makedirs(backup_dir, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, os.path.join(backup_dir, f"articles_{stamp}.json"))

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cap": schema.MAX_ARCHIVE_RECORDS,
        "per_ticker_floor": schema.PER_TICKER_FLOOR,
        "collect_from": schema.COLLECT_FROM,
        "entities": entities,
        "count": len(records),
        "signature": signature(records),
        "articles": records,
    }

    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp creates 0600 and os.replace preserves it; this file is
        # published by a web server, so widen it to the usual 0644.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
