#!/usr/bin/env python3
"""Collect portfolio news headlines into the dashboard archive.

    python -m tools.collect --dry-run              # fetch, report, write nothing
    python -m tools.collect                        # normal daily run
    python -m tools.collect --backfill             # first run: since COLLECT_FROM
    python -m tools.collect --only "743 HK Equity" # one company, for debugging
    python -m tools.collect --sources gdelt        # restrict sources

Deterministic by design: the same feeds and the same entity file produce the
same archive.

Built to survive an unattended CI runner:

  * a wall-clock budget, so the run finishes and writes what it has instead of
    being killed halfway with nothing to show for it;
  * a circuit breaker per source, so an outage costs one round of retries
    rather than seventy-four;
  * SIGTERM and SIGINT become an orderly finish, not a lost run;
  * the archive is written only when the article set actually changed, so a
    quiet day produces no commit;
  * the outcome is published to GITHUB_OUTPUT and the run summary.

Exit codes
    0  ran to completion (or stopped on budget) with usable results
    1  every requested source failed - nothing could be collected
    2  configuration error: bad arguments or a rejected entity file
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import signal
import sys
import time

if __package__ in (None, ""):                      # allow direct invocation
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import archive, entities as ent, matching, schema
from tools.sources import gdelt, gnews

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_FILE = os.path.join(REPO, "data", "entities.csv")
ARCHIVE_FILE = os.path.join(REPO, "docs", "data", "articles.json")
BACKUP_DIR = os.path.join(REPO, "backups")

# A source that fails this many times in a row is assumed down and skipped for
# the rest of the run. Without it, an outage costs 74 entities x 3 retries x
# the timeout, which on its own can exceed the job's whole time budget.
BREAKER_THRESHOLD = 5

_stop = False


def log(msg: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def _request_stop(signum, _frame):
    """Turn a runner cancellation into an orderly finish rather than a kill."""
    global _stop
    _stop = True
    log(f"signal {signum} received - finishing the current company and writing out")


class Breaker:
    """Tracks consecutive failures per source and trips when one looks down."""

    def __init__(self, sources: set[str]):
        self.streak = {s: 0 for s in sources}
        self.errors = {s: 0 for s in sources}
        self.tripped: set[str] = set()

    def live(self, source: str) -> bool:
        return source not in self.tripped

    def ok(self, source: str) -> None:
        self.streak[source] = 0

    def fail(self, source: str) -> None:
        self.errors[source] += 1
        self.streak[source] += 1
        if self.streak[source] >= BREAKER_THRESHOLD and source not in self.tripped:
            self.tripped.add(source)
            log(f"  ! {source} failed {BREAKER_THRESHOLD} times running - "
                f"skipping it for the rest of this run")


def collect_for(entity, start: dt.date, end: dt.date, sources: set[str],
                breaker: Breaker, stats: dict) -> list[dict]:
    """Fetch, score and cap the records for one entity."""
    raws: list[tuple[str, dict]] = []

    if "gdelt" in sources and breaker.live("gdelt"):
        try:
            for raw in gdelt.fetch_articles(entity, start, end):
                raws.append(("gdelt", raw))
            breaker.ok("gdelt")
        except Exception as exc:                       # noqa: BLE001
            breaker.fail("gdelt")
            log(f"  ! gdelt failed for {entity.ticker}: {exc}")

    if "gnews" in sources and breaker.live("gnews"):
        try:
            for raw in gnews.fetch_articles(entity):
                raws.append(("gnews", raw))
            breaker.ok("gnews")
        except Exception as exc:                       # noqa: BLE001
            breaker.fail("gnews")
            log(f"  ! gnews failed for {entity.ticker}: {exc}")

    start_iso = start.strftime("%Y-%m-%dT00:00:00Z")
    floor_iso = max(start_iso, schema.COLLECT_FROM + "T00:00:00Z")

    records: list[dict] = []
    for source, raw in raws:
        stats["seen"] += 1
        if raw.get("published", "") < floor_iso:
            stats["too_old"] += 1
            continue
        verdict, _reason = matching.score(
            entity, raw.get("headline", ""), raw.get("publisher", ""))
        if verdict == matching.REJECT:
            stats["rejected"] += 1
            continue
        rec = archive.make_record(entity, raw, source, verdict)
        if rec is None:
            stats["unusable"] += 1
            continue
        records.append(rec)
        stats[verdict] += 1

    records.sort(key=lambda r: r["published"], reverse=True)
    if len(records) > schema.MAX_PER_TICKER_PER_RUN:
        stats["capped"] += len(records) - schema.MAX_PER_TICKER_PER_RUN
        records = records[: schema.MAX_PER_TICKER_PER_RUN]
    return records


def emit_outputs(**values) -> None:
    """Publish results to GITHUB_OUTPUT so later workflow steps can branch."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in values.items():
                fh.write(f"{key}={value}\n")
    except OSError as exc:
        log(f"could not write GITHUB_OUTPUT: {exc}")


def emit_summary(lines: list[str]) -> None:
    """Publish a readable report to the Actions run summary page."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        log(f"could not write GITHUB_STEP_SUMMARY: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, but do not write the archive")
    ap.add_argument("--backfill", action="store_true",
                    help=f"search back to {schema.COLLECT_FROM} instead of --days")
    ap.add_argument("--days", type=int, default=3,
                    help="lookback window in days for a normal run (default 3)")
    ap.add_argument("--only", action="append", default=None, metavar="TICKER",
                    help="restrict to one ticker; repeatable")
    ap.add_argument("--sources", default="gdelt,gnews",
                    help="comma-separated subset of: gdelt, gnews")
    ap.add_argument("--budget-minutes", type=float, default=30.0,
                    help="stop fetching after this long and write what was found "
                         "(default 30; 0 disables)")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    sources = {s.strip().lower() for s in args.sources.split(",") if s.strip()}
    unknown = sources - set(schema.SOURCES)
    if unknown:
        print(f"unknown source(s): {sorted(unknown)}", file=sys.stderr)
        return 2
    if not sources:
        print("no sources selected", file=sys.stderr)
        return 2

    try:
        all_entities = ent.load(ENTITY_FILE)
    except ent.EntityError as exc:
        print(f"\nENTITY FILE REJECTED:\n{exc}\n", file=sys.stderr)
        emit_summary(["## News collection failed", "",
                      "The entity file was rejected, so nothing was collected.",
                      "", "```", str(exc)[:1500], "```"])
        emit_outputs(changed="false", count=0, new=0, status="entity-file-rejected")
        return 2

    entities = all_entities
    if args.only:
        wanted = set(args.only)
        entities = [e for e in all_entities if e.ticker in wanted]
        if not entities:
            print(f"no entity matched {sorted(wanted)}", file=sys.stderr)
            return 2

    today = dt.datetime.now(dt.timezone.utc).date()
    floor = dt.date.fromisoformat(schema.COLLECT_FROM)
    start = floor if args.backfill else max(floor, today - dt.timedelta(days=args.days))

    log(f"entities: {len(entities)} of {len(all_entities)} | "
        f"window: {start} to {today} | sources: {sorted(sources)} | "
        f"budget: {args.budget_minutes or 'none'} min")

    deadline = (time.monotonic() + args.budget_minutes * 60
                if args.budget_minutes > 0 else None)

    stats = dict(seen=0, high=0, low=0, rejected=0, too_old=0, unusable=0, capped=0)
    breaker = Breaker(sources)
    incoming: list[dict] = []
    processed = 0
    halted = ""

    for i, entity in enumerate(entities, 1):
        if _stop:
            halted = "cancelled"
            break
        if deadline and time.monotonic() > deadline:
            halted = "time budget"
            log(f"time budget reached after {processed} companies - writing what was found")
            break
        if breaker.tripped >= sources:
            halted = "all sources down"
            log("every source has tripped its breaker - stopping early")
            break

        incoming.extend(collect_for(entity, start, today, sources, breaker, stats))
        processed = i
        if i % 10 == 0 or i == len(entities):
            log(f"  {i}/{len(entities)} processed, {len(incoming)} records so far")

    existing = archive.load(ARCHIVE_FILE)
    merged, added = archive.merge(existing, incoming)
    final = archive.apply_window(merged)

    changed = archive.signature(existing) != archive.signature(final)
    total_errors = sum(breaker.errors.values())
    dead = sorted(breaker.tripped)

    print("\n=== Collection report ===")
    print(f"  companies processed: {processed} of {len(entities)}"
          + (f"  (stopped early: {halted})" if halted else ""))
    print(f"  feed items seen   : {stats['seen']}")
    print(f"  matched high      : {stats['high']}")
    print(f"  matched low       : {stats['low']}")
    print(f"  rejected (no match/negative): {stats['rejected']}")
    print(f"  outside window    : {stats['too_old']}")
    print(f"  unusable records  : {stats['unusable']}")
    print(f"  trimmed by per-run cap     : {stats['capped']}")
    print(f"  source errors     : {total_errors}" + (f"  (down: {dead})" if dead else ""))
    print(f"  archive before    : {len(existing)}")
    print(f"  new after dedupe  : {added}")
    print(f"  archive after cap : {len(final)} (cap {schema.MAX_ARCHIVE_RECORDS})")
    print(f"  archive changed   : {'yes' if changed else 'no'}")

    emit_summary([
        "## Portfolio news collection", "",
        "| | |", "|---|---|",
        f"| Window | {start} to {today} |",
        f"| Companies processed | {processed} of {len(entities)}"
        + (f" (stopped early: {halted})" if halted else "") + " |",
        f"| Headlines seen | {stats['seen']} |",
        f"| Confirmed matches | {stats['high']} |",
        f"| Flagged for review | {stats['low']} |",
        f"| New after dedupe | {added} |",
        f"| Archive size | {len(final)} of {schema.MAX_ARCHIVE_RECORDS} |",
        f"| Source errors | {total_errors}"
        + (f" - unavailable: {', '.join(dead)}" if dead else "") + " |",
        f"| Archive changed | {'yes' if changed else 'no'} |",
    ])

    if breaker.tripped >= sources:
        emit_outputs(changed="false", count=len(final), new=0, status="sources-down")
        print("\n  ALL SOURCES UNAVAILABLE - archive left untouched.\n", file=sys.stderr)
        return 1

    if args.dry_run:
        print("  DRY RUN - nothing written.\n")
        emit_outputs(changed="false", count=len(final), new=added, status="dry-run")
        return 0

    if not changed:
        print("  no change to the article set - archive left untouched.\n")
        emit_outputs(changed="false", count=len(final), new=0, status="unchanged")
        return 0

    with archive.Lock(ARCHIVE_FILE):
        archive.save(ARCHIVE_FILE, final,
                     entities=archive.entity_manifest(all_entities),
                     backup_dir=BACKUP_DIR)
    print(f"  written           : {os.path.relpath(ARCHIVE_FILE, REPO)}\n")
    emit_outputs(changed="true", count=len(final), new=added, status="ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
