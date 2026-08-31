#!/usr/bin/env python3
"""Test suite for the portfolio news monitor.

    python tests/test_all.py

Covers the four things that would hurt most if they broke: the entity file
being wrong, a wrong article being attributed to a company, a hostile feed
reaching the browser or the network layer, and the archive losing coverage of
a quiet company when it trims.

No network access is required — sources are exercised against fixtures.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import archive, entities as ent, matching, net, schema
from tools.sources_allowlist import is_sri_lankan
from tools.sources import gdelt, gnews

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTITY_FILE = os.path.join(REPO, "data", "entities.csv")

PASSED, FAILED = [], []


def check(name):
    def wrap(fn):
        try:
            fn()
            PASSED.append(name)
        except AssertionError as exc:
            FAILED.append((name, str(exc) or "assertion failed"))
        except Exception as exc:                      # noqa: BLE001
            FAILED.append((name, f"{type(exc).__name__}: {exc}"))
        return fn
    return wrap


def entity(**kw):
    base = dict(ticker="T", name="Test Co", aliases=["Test Co"], context_terms=[],
                negative_terms=[], country="X", industry="Y", ambiguous=False,
                status="active", note="", verified="", weak=set(),
                required_terms=[], kind="company", flag_terms=[])
    base.update(kw)
    return ent.Entity(**base).compile()


# ---------------------------------------------------------------- entity file

@check("real entity file loads: 74 holdings plus 6 macro rows")
def _t1():
    loaded = ent.load(ENTITY_FILE)
    companies = [e for e in loaded if e.kind == "company"]
    macros = [e for e in loaded if e.kind == "macro"]
    assert len(companies) == 74, f"expected 74 companies, got {len(companies)}"
    assert len(macros) == 6, f"expected 6 macro rows, got {len(macros)}"
    assert len({e.ticker for e in loaded}) == len(loaded), "tickers are not unique"
    for m in macros:
        assert m.required_terms, f"{m.ticker} is macro but unscoped"
    for e in loaded:
        assert e.aliases, f"{e.ticker} has no aliases"
        if e.ambiguous:
            assert e.context_terms, f"{e.ticker} is ambiguous but has no context terms"


@check("every alias is stored accent-folded so it can match folded headlines")
def _t2():
    for e in ent.load(ENTITY_FILE):
        for alias in e.aliases:
            assert matching.normalise(alias) == alias, \
                f"{e.ticker}: alias {alias!r} is not accent-folded"


@check("entity file rejects a row missing required fields")
def _t3():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "e.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(",".join(schema.ENTITY_COLS) + "\n")
            fh.write("T,,Alias,,,,X,Y,no,active,,,company,\n")   # name blank
        try:
            ent.load(path)
            raise AssertionError("expected EntityError for a blank name")
        except ent.EntityError as exc:
            assert "name is required" in str(exc), exc


@check("entity file rejects an ambiguous row with no context terms")
def _t4():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "e.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(",".join(schema.ENTITY_COLS) + "\n")
            fh.write("T,Name,Alias Here,,,,X,Y,yes,active,,,company,\n")
        try:
            ent.load(path)
            raise AssertionError("expected EntityError for missing context terms")
        except ent.EntityError as exc:
            assert "context term" in str(exc), exc


# ---------------------------------------------------------------- matching

@check("distinctive name matches; unrelated headline does not")
def _t5():
    e = entity(aliases=["Tokyo Cement"])
    assert matching.score(e, "Tokyo Cement posts higher quarterly profit")[0] == matching.HIGH
    assert matching.score(e, "Cement prices rise across South Asia")[0] == matching.REJECT


@check("accented headline matches the folded alias")
def _t6():
    e = entity(aliases=["Tofas"])
    assert matching.score(e, "Tofas, Bursa'da uretimi artiriyor")[0] == matching.HIGH
    assert matching.score(e, "Tofaş üretim rakamlarını açıkladı")[0] == matching.HIGH


@check("ambiguous name without corroboration is flagged low, not dropped")
def _t7():
    e = entity(aliases=["Peabody"], weak={"Peabody"},
               ambiguous=True, context_terms=["coal", "mining"])
    low, _ = matching.score(e, "Peabody Award winners announced in Manhattan")
    assert low == matching.LOW, low
    high, _ = matching.score(e, "Peabody lifts coal output guidance")
    assert high == matching.HIGH, high


@check("a strong alias outranks a weak one in the same headline")
def _t7b():
    e = entity(aliases=["Peabody Energy", "Peabody"], weak={"Peabody"},
               ambiguous=True, context_terms=["coal"])
    verdict, reason = matching.score(e, "Peabody Energy names new chief executive")
    assert verdict == matching.HIGH, (verdict, reason)
    assert "Peabody Energy" in reason, reason


@check("the ambiguous flag must agree with the weak-alias markers")
def _t7c():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "e.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(",".join(schema.ENTITY_COLS) + "\n")
            fh.write("T,Name,Alias Here,coal,,,X,Y,yes,active,,,company,\n")  # yes, no ~
        try:
            ent.load(path)
            raise AssertionError("expected EntityError for flag/marker mismatch")
        except ent.EntityError as exc:
            assert "marked weak" in str(exc), exc


@check("a hard block deletes an article that names only the wrong subject")
def _t8():
    e = entity(aliases=["Blackstone Minerals", "Blackstone"],
               weak={"Blackstone"}, ambiguous=True,
               context_terms=["nickel"], negative_terms=["Blackstone Inc"])
    # Weak alias + block term: almost certainly the other Blackstone.
    verdict, reason = matching.score(e, "Blackstone Inc closes property fund")
    assert verdict == matching.REJECT, (verdict, reason)


@check("an exact company name outranks a hard block, but is flagged")
def _t8b():
    # Previously this article was deleted, which lost real coverage: the
    # holding is named outright in the headline. It is now kept and graded
    # low so the analyst decides.
    e = entity(aliases=["Blackstone Minerals", "Blackstone"],
               weak={"Blackstone"}, ambiguous=True,
               context_terms=["nickel"], negative_terms=["Blackstone Inc"])
    verdict, reason = matching.score(
        e, "Blackstone Inc and Blackstone Minerals both named in nickel report")
    assert verdict == matching.LOW, (verdict, reason)
    assert "Blackstone Minerals" in reason, reason


@check("a flag term keeps the article but downgrades it")
def _t8c():
    e = entity(aliases=["Blackstone Minerals"], flag_terms=["private equity"])
    verdict, reason = matching.score(
        e, "Blackstone Minerals secures private equity backing")
    assert verdict == matching.LOW, (verdict, reason)
    assert "private equity" in reason, reason
    # Without the flag term the same alias confirms outright.
    assert matching.score(e, "Blackstone Minerals lifts nickel resource")[0] \
        == matching.HIGH


@check("a term cannot both block and flag the same article")
def _t8d():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "e.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(",".join(schema.ENTITY_COLS) + "\n")
            fh.write("T,Name,Alias Here,,museum,museum,X,Y,no,active,,,company,\n")
        try:
            ent.load(path)
            raise AssertionError("expected EntityError for a term in both tiers")
        except ent.EntityError as exc:
            assert "both negative_terms and flag_terms" in str(exc), exc


@check("macro rows exclude cricket and other non-economic Sri Lankan news")
def _t8e():
    loaded = {e.ticker: e for e in ent.load(ENTITY_FILE)}
    real = loaded["SL MACRO REAL"]
    assert matching.score(real, "Sri Lanka cricket board reports growth")[0] \
        == matching.REJECT
    assert matching.score(real, "Sri Lanka GDP growth beats forecast")[0] \
        == matching.HIGH
    for m in (e for e in loaded.values() if e.kind == "macro"):
        assert "cricket" in [t.lower() for t in m.negative_terms], \
            f"{m.ticker} does not exclude cricket"


@check("no entity has a term in both exclusion tiers")
def _t8f():
    for e in ent.load(ENTITY_FILE):
        overlap = ({t.lower() for t in e.negative_terms}
                   & {t.lower() for t in e.flag_terms})
        assert not overlap, f"{e.ticker}: {sorted(overlap)} in both tiers"


@check("alias matches only on word boundaries")
def _t9():
    e = entity(aliases=["Karoon"])
    assert matching.score(e, "Karoonia Holdings reports results")[0] == matching.REJECT
    assert matching.score(e, "Karoon lifts output")[0] == matching.HIGH


@check("aliases with punctuation still match")
def _t10():
    e = entity(aliases=["Thai O.P.P.", "King's Flair"])
    assert matching.score(e, "Thai O.P.P. reports higher film sales")[0] == matching.HIGH
    assert matching.score(e, "King's Flair expands houseware range")[0] == matching.HIGH


@check("publisher alone never creates a match")
def _t11():
    e = entity(aliases=["Tokyo Cement"], weak={"Tokyo Cement"},
               ambiguous=True, context_terms=["Sri Lanka"])
    verdict, _ = matching.score(e, "Rupee steadies against the dollar", "Sri Lanka Daily")
    assert verdict == matching.REJECT, verdict


@check("regex metacharacters in an alias are treated as literal text")
def _t12():
    e = entity(aliases=["Asia Cement (China)"])
    assert matching.score(e, "Asia Cement (China) lifts clinker sales")[0] == matching.HIGH
    assert matching.score(e, "Asia Cement XChinaX reports")[0] == matching.REJECT


# ---------------------------------------------------------------- macro scoping

@check("a scoped row ignores the same story about another country")
def _t12b():
    e = entity(kind="macro", aliases=["inflation"], weak={"inflation"},
               ambiguous=True, context_terms=["Sri Lanka"],
               required_terms=["Sri Lanka", "CBSL"])
    assert matching.score(e, "US inflation cools to 2.1%")[0] == matching.REJECT
    assert matching.score(e, "Bank of England warns on inflation")[0] == matching.REJECT
    assert matching.score(e, "Sri Lanka inflation eases to 3.2%")[0] == matching.HIGH


@check("a strong macro alias is its own proof of scope")
def _t12c():
    # AWPLR and CCPI are Sri Lankan by construction: they must confirm without
    # a separate marker, and must be tested BEFORE the scope gate runs.
    e = entity(kind="macro", aliases=["AWPLR", "inflation"], weak={"inflation"},
               ambiguous=True, context_terms=["Sri Lanka"],
               required_terms=["Sri Lanka"])
    verdict, reason = matching.score(e, "AWPLR falls to 8.4 percent", "Daily FT")
    assert verdict == matching.HIGH, (verdict, reason)
    assert "AWPLR" in reason, reason


@check("scope from a Sri Lankan publisher is flagged, not asserted")
def _t12d():
    e = entity(kind="macro", aliases=["inflation"], weak={"inflation"},
               ambiguous=True, context_terms=["Sri Lanka"],
               required_terms=["Sri Lanka"])
    # A verified Sri Lankan DOMAIN proves origin, so the article is kept - but
    # graded low, since a Sri Lankan paper also reports on the wider world.
    verdict, reason = matching.score(
        e, "Inflation eases to 3.2% in August", "economynext.com")
    assert verdict == matching.LOW, (verdict, reason)
    assert "publisher" in reason, reason

    # A publisher NAME proves nothing - "Daily Mirror" and "Sunday Times" are
    # UK papers. Only the domain counts.
    assert matching.score(e, "Inflation eases in August", "Daily Mirror")[0] \
        == matching.REJECT


@check("macro rows must declare required_terms or be refused")
def _t12e():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "e.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(",".join(schema.ENTITY_COLS) + "\n")
            fh.write("M,Theme,~inflation,coal,,,LK,X,yes,active,,,macro,\n")
        try:
            ent.load(path)
            raise AssertionError("expected EntityError for an unscoped macro row")
        except ent.EntityError as exc:
            assert "required_terms" in str(exc), exc


@check("the scope block is ANDed into the feed query, not just filtered after")
def _t12f():
    e = entity(kind="macro", aliases=["inflation", "policy rate"],
               weak={"inflation", "policy rate"}, ambiguous=True,
               context_terms=["Sri Lanka"], required_terms=["Sri Lanka", "CBSL"])
    for q in gdelt.build_queries(e) + gnews.build_queries(e):
        assert "Sri Lanka" in q or "CBSL" in q, f"unscoped weak-term query: {q}"


@check("strong aliases are queried unscoped so their coverage is not narrowed")
def _t12g():
    e = entity(kind="macro", aliases=["AWPLR", "inflation"], weak={"inflation"},
               ambiguous=True, context_terms=["Sri Lanka"],
               required_terms=["Sri Lanka"])
    queries = gdelt.build_queries(e)
    strong_q = [q for q in queries if "AWPLR" in q]
    assert strong_q, queries
    assert "Sri Lanka" not in strong_q[0], \
        "a self-scoping strong alias must not be narrowed by the scope block"


@check("no row's query plan is silently truncated")
def _t12h():
    for e in ent.load(ENTITY_FILE):
        for mod in (gdelt, gnews):
            per = mod.MAX_ALIASES_PER_QUERY
            strong = len([a for a in e.aliases if a not in e.weak])
            weak = len([a for a in e.aliases if a in e.weak])
            need = -(-strong // per) + -(-weak // per)
            assert need <= schema.MAX_QUERY_CHUNKS, (
                f"{e.ticker} needs {need} chunks for {mod.__name__}, "
                f"limit is {schema.MAX_QUERY_CHUNKS} — tail terms would be lost")


@check("macro volume cannot crowd the equities out of the archive")
def _t12i():
    macro = [{"id": f"M{i}", "ticker": "SL MACRO MONETARY", "kind": "macro",
              "published": f"2026-08-23T00:{i % 60:02d}:00Z"} for i in range(900)]
    company = [{"id": f"C{i}", "ticker": f"CO{i % 20}", "kind": "company",
                "published": f"2026-08-0{(i % 9) + 1}T00:00:00Z"} for i in range(300)]
    out = archive.apply_window(macro + company, cap=400, floor=5, macro_share=0.4)
    assert len(out) == 400
    n_macro = sum(1 for r in out if r["kind"] == "macro")
    assert n_macro <= 0.4 * 400 + 5, f"macro took {n_macro} of 400"
    assert sum(1 for r in out if r["kind"] == "company") >= 200


@check("REGRESSION: every scoped query actually contains a Sri Lanka marker")
def _t12j():
    # The bug this pins: scope terms were sorted by length, which picked the
    # longest — outlet names like "Sunday Observer" — and dropped "Sri Lanka"
    # from the query entirely. The feeds were then asked for UK newspaper
    # coverage and returned the world's economics news.
    for e in ent.load(ENTITY_FILE):
        if e.kind != "macro":
            continue
        for mod in (gdelt, gnews):
            for q in mod.build_queries(e):
                weak_in_q = [w for w in e.weak if f'"{w}"' in q]
                if not weak_in_q:
                    continue
                assert "Lanka" in q, (
                    f"{e.ticker}: generic terms {weak_in_q} queried without a "
                    f"Sri Lanka marker — query was: {q[:160]}")


@check("REGRESSION: no scope term is a name another country's press also uses")
def _t12k():
    # "Daily Mirror", "Sunday Times", "Sunday Observer", "The Island" and
    # "Daily News" are all UK or US publications too, and "rupee" belongs to
    # five other countries. None may be used to prove Sri Lankan scope.
    forbidden = {"daily mirror", "sunday times", "sunday observer", "the island",
                 "daily news", "rupee", "daily ft", "newsfirst", "ceylon today",
                 "ada derana", "economynext"}
    for e in ent.load(ENTITY_FILE):
        clash = {t.lower() for t in e.required_terms} & forbidden
        assert not clash, (
            f"{e.ticker}: {sorted(clash)} cannot prove Sri Lankan scope — "
            f"use a .lk domain check instead")


@check("Sri Lankan outlets are recognised by domain, not by name")
def _t12l():
    assert matching.from_lk_source("dailymirror.lk")
    assert matching.from_lk_source("ft.lk")
    assert matching.from_lk_source("www.economynext.lk")
    # The UK papers that broke this must NOT register.
    assert not matching.from_lk_source("Daily Mirror")
    assert not matching.from_lk_source("Sunday Times")
    assert not matching.from_lk_source("mirror.co.uk")
    assert not matching.from_lk_source("")


@check("a weak company alias is never queried bare")
def _t12m():
    # "Peabody" or "Cavendish" on its own returns the whole world and leaves
    # the matcher to discard almost all of it. Every weak alias must be
    # narrowed at the source by its context terms.
    for e in ent.load(ENTITY_FILE):
        if e.kind != "company" or not e.weak:
            continue
        for mod in (gdelt, gnews):
            for q in mod.build_queries(e):
                if not any(f'"{w}"' in q for w in e.weak):
                    continue
                assert ") (" in q or q.rstrip().endswith(")") and " (" in q, (
                    f"{e.ticker}: weak alias queried without narrowing: {q[:120]}")


@check("foreign economic news never reaches a macro row")
def _t12n():
    loaded = {e.ticker: e for e in ent.load(ENTITY_FILE)}
    cases = [
        ("SL MACRO MONETARY", "UK inflation falls to 2.1%", "Daily Mirror"),
        ("SL MACRO MONETARY", "Bank of England cuts rates", "Sunday Times"),
        ("SL MACRO FISCAL", "US budget deficit widens", "Sunday Observer"),
        ("SL MACRO REAL", "Nepal GDP growth slows", "Kathmandu Post"),
        ("SL MACRO EXTERNAL", "Pakistan rupee slides", "Dawn"),
    ]
    for ticker, headline, publisher in cases:
        verdict, reason = matching.score(loaded[ticker], headline, publisher)
        assert verdict == matching.REJECT, (headline, publisher, verdict, reason)

    # ...while genuine Sri Lankan coverage still confirms.
    mon = loaded["SL MACRO MONETARY"]
    assert matching.score(mon, "Sri Lanka inflation eases to 3.2%", "reuters.com")[0] \
        == matching.HIGH
    assert matching.score(mon, "CBSL holds policy rate", "reuters.com")[0] \
        == matching.HIGH
    # A .lk outlet with no country in the headline is kept, but flagged.
    assert matching.score(mon, "Inflation eases in August", "dailymirror.lk")[0] \
        == matching.LOW


@check("the publisher allowlist accepts Sri Lankan outlets only")
def _t12o():
    for good in ("ft.lk", "www.dailymirror.lk", "bizenglish.adaderana.lk",
                 "economynext.com", "https://island.lk/story"):
        assert is_sri_lankan(good), good
    for bad in ("reuters.com", "mirror.co.uk", "thetimes.co.uk", "",
                "timesofindia.com", "dailymirror.lk.evil.com", "notlk.com"):
        assert not is_sri_lankan(bad), bad


@check("macro scope is an allowlist: unproven origin is refused")
def _t12p():
    loaded = {e.ticker: e for e in ent.load(ENTITY_FILE)}
    mon = loaded["SL MACRO MONETARY"]
    # Foreign publisher and no marker in the headline: refused, whatever the
    # feed returned. This is the fail-closed behaviour that replaced the old
    # filter-afterwards approach.
    for headline, publisher in [
        ("Inflation eases to 2.1% in August", "reuters.com"),
        ("Central bank holds policy rate", "bloomberg.com"),
        ("UK inflation falls", "mirror.co.uk"),
    ]:
        verdict, reason = matching.score(mon, headline, publisher)
        assert verdict == matching.REJECT, (headline, publisher, verdict)
        assert "provably" in reason or "scope" in reason, reason

    # Proven Sri Lankan by publisher: kept, flagged.
    assert matching.score(mon, "Inflation eases in August", "economynext.com")[0] \
        == matching.LOW
    # Proven by the headline itself: confirmed, from any publisher.
    assert matching.score(mon, "Sri Lanka inflation eases", "reuters.com")[0] \
        == matching.HIGH


@check("macro queries carry GDELT's source-country restriction")
def _t12q():
    for e in ent.load(ENTITY_FILE):
        for q in gdelt.build_queries(e):
            if e.kind == "macro":
                assert "sourcecountry:CE" in q, f"{e.ticker} unrestricted: {q[:120]}"
            else:
                assert "sourcecountry" not in q, f"{e.ticker} should not be restricted"


@check("Google News is not used for macro rows")
def _t12r():
    # Its search ignores boolean scope and its links hide the real publisher,
    # so it can satisfy neither the country restriction nor the allowlist.
    from tools import collect as col
    loaded = {e.ticker: e for e in ent.load(ENTITY_FILE)}
    called = []

    def fake_gnews(entity, fetcher=None):
        called.append(entity.ticker)
        return []

    orig = col.gnews.fetch_articles
    col.gnews.fetch_articles = fake_gnews
    try:
        breaker = col.Breaker({"gnews"})
        stats = dict(seen=0, high=0, low=0, rejected=0, too_old=0,
                     unusable=0, capped=0)
        col.collect_for(loaded["SL MACRO MONETARY"], dt.date(2026, 8, 1),
                        dt.date(2026, 8, 2), {"gnews"}, breaker, stats)
        col.collect_for(loaded["TKYO SL Equity"], dt.date(2026, 8, 1),
                        dt.date(2026, 8, 2), {"gnews"}, breaker, stats)
    finally:
        col.gnews.fetch_articles = orig
    assert "SL MACRO MONETARY" not in called, "macro row queried Google News"
    assert "TKYO SL Equity" in called, "company row should still use Google News"


@check("stored records are re-scored, so a rule fix cleans the archive")
def _t12s():
    # The archive used to be append-only: anything collected under a rule that
    # later proved wrong stayed until the cap evicted it, so a correction
    # appeared to do nothing. Re-scoring makes fixes retroactive.
    entities = ent.load(ENTITY_FILE)
    loaded = {e.ticker: e for e in entities}
    mon = loaded["SL MACRO MONETARY"]

    junk = archive.make_record(
        mon, {"url": "https://reuters.com/a", "headline": "UK inflation falls to 2.1%",
              "published": "2026-08-01T00:00:00Z", "publisher": "reuters.com"},
        "gdelt", "high")
    good = archive.make_record(
        mon, {"url": "https://reuters.com/b", "headline": "Sri Lanka inflation eases",
              "published": "2026-08-02T00:00:00Z", "publisher": "reuters.com"},
        "gdelt", "high")
    orphan = dict(good, id="orphan1", ticker="GONE XX Equity")

    kept, dropped = archive.revalidate([junk, good, orphan], entities)
    kept_ids = {r["id"] for r in kept}
    assert good["id"] in kept_ids, "genuine article was purged"
    assert junk["id"] not in kept_ids, "foreign article survived revalidation"
    assert "orphan1" not in kept_ids, "record for a removed entity survived"
    assert len(dropped) == 2


@check("revalidation refreshes the grade, not just membership")
def _t12t():
    entities = ent.load(ENTITY_FILE)
    mon = {e.ticker: e for e in entities}["SL MACRO MONETARY"]
    # Stored as confirmed, but under current rules it is only publisher-scoped.
    rec = archive.make_record(
        mon, {"url": "https://economynext.com/x", "headline": "Inflation eases in August",
              "published": "2026-08-02T00:00:00Z", "publisher": "economynext.com"},
        "gdelt", "high")
    rec["confidence"] = "high"
    kept, _ = archive.revalidate([rec], entities)
    assert kept and kept[0]["confidence"] == "low", kept


# ---------------------------------------------------------------- security

@check("URLs off the allowlist, and private addresses, are refused")
def _t13():
    for bad in ["https://evil.example.com/x",
                "http://169.254.169.254/latest/meta-data/",
                "file:///etc/passwd",
                "https://localhost/x"]:
        try:
            net.validate_url(bad)
            raise AssertionError(f"should have refused {bad}")
        except net.FetchError:
            pass
    net.validate_url("https://api.gdeltproject.org/api/v2/doc/doc?query=x")


@check("javascript: and data: article links never become records")
def _t14():
    e = entity()
    for bad in ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
                "vbscript:msgbox(1)", ""]:
        rec = archive.make_record(
            e, {"url": bad, "headline": "H", "published": "2026-08-01T00:00:00Z"},
            "gdelt", "high")
        assert rec is None, f"accepted unsafe URL {bad!r}"
    ok = archive.make_record(
        e, {"url": "https://example.com/a", "headline": "H",
            "published": "2026-08-01T00:00:00Z"}, "gdelt", "high")
    assert ok is not None and ok["url"].startswith("https://")


@check("markup in a headline survives as text, never as a record field break")
def _t15():
    e = entity(aliases=["Test Co"])
    payload = '<img src=x onerror=alert(1)> Test Co "quoted" & </script>'
    verdict, _ = matching.score(e, payload)
    assert verdict == matching.HIGH
    rec = archive.make_record(
        e, {"url": "https://example.com/a", "headline": payload,
            "published": "2026-08-01T00:00:00Z"}, "gdelt", "high")
    assert rec is not None
    # Survives a JSON round trip unchanged; the browser renders it via
    # textContent, so it is displayed rather than parsed.
    assert json.loads(json.dumps(rec))["headline"] == payload


@check("an XML entity-expansion attack in an RSS feed yields nothing")
def _t16():
    bomb = b"""<?xml version="1.0"?>
    <!DOCTYPE rss [
      <!ENTITY a "aaaaaaaaaa">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
      <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
    ]>
    <rss><channel><item><title>&c;</title><link>http://x/</link></item></channel></rss>"""
    assert gnews.parse(bomb) == []

    xxe = b"""<?xml version="1.0"?>
    <!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <rss><channel><item><title>&xxe;</title><link>http://x/</link></item></channel></rss>"""
    assert gnews.parse(xxe) == []


@check("malformed source payloads degrade to empty, never raise")
def _t17():
    assert gdelt.parse(b"<html>gateway timeout</html>") == []
    assert gdelt.parse(b'{"articles": "not a list"}') == []
    assert gdelt.parse(b"") == []
    assert gnews.parse(b"not xml at all") == []


# ---------------------------------------------------------------- parsers

@check("GDELT ArtList parses into normalised records")
def _t18():
    body = json.dumps({"articles": [
        {"url": "https://ex.com/1", "title": "Yancoal lifts output",
         "seendate": "20260812T101500Z", "domain": "ex.com"},
        {"url": "", "title": "no url", "seendate": "20260812T101500Z"},
        {"url": "https://ex.com/2", "title": "bad date", "seendate": "nonsense"},
    ]}).encode()
    out = gdelt.parse(body)
    assert len(out) == 1, out
    assert out[0]["published"] == "2026-08-12T10:15:00Z", out[0]
    assert out[0]["headline"] == "Yancoal lifts output"


@check("Google News RSS parses and separates the publisher from the headline")
def _t19():
    feed = b"""<rss version="2.0"><channel>
      <item><title>Tokyo Cement posts higher profit - Daily Mirror</title>
        <link>https://news.google.com/rss/articles/abc</link>
        <pubDate>Wed, 12 Aug 2026 10:15:00 GMT</pubDate>
        <source url="https://dailymirror.lk">Daily Mirror</source></item>
      <item><title>A headline - with a dash - Reuters</title>
        <link>https://news.google.com/rss/articles/def</link>
        <pubDate>Wed, 12 Aug 2026 09:00:00 GMT</pubDate>
        <source url="https://reuters.com">Reuters</source></item>
    </channel></rss>"""
    out = gnews.parse(feed)
    assert len(out) == 2, out
    assert out[0]["headline"] == "Tokyo Cement posts higher profit", out[0]
    assert out[0]["publisher"] == "Daily Mirror"
    assert out[0]["published"] == "2026-08-12T10:15:00Z"
    # A dash inside the headline must not truncate it.
    assert out[1]["headline"] == "A headline - with a dash", out[1]


@check("GDELT query builder emits a quoted OR block")
def _t20():
    e = entity(aliases=["Tokyo Cement", "TKYO"])
    query = gdelt.build_query(e)
    assert query.startswith("(") and " OR " in query and '"Tokyo Cement"' in query, query


# ---------------------------------------------------------------- archive

@check("tracking parameters are stripped so the same story dedupes")
def _t21():
    a = matching.canonical_url("https://ex.com/story?id=7&utm_source=x&fbclid=y")
    b = matching.canonical_url("https://EX.com/story/?id=7")
    assert a == b, (a, b)
    # A real article id in the query must survive.
    assert "id=7" in a


@check("merge is idempotent and keeps the first-seen record")
def _t22():
    e = entity()
    raw = {"url": "https://ex.com/a", "headline": "Headline one",
           "published": "2026-08-01T00:00:00Z"}
    rec = archive.make_record(e, raw, "gdelt", "high")
    first, added1 = archive.merge([], [rec])
    second, added2 = archive.merge(first, [rec])
    assert added1 == 1 and added2 == 0
    assert len(second) == 1


@check("the same story from two outlets collapses to one record")
def _t23():
    e = entity()
    a = archive.make_record(e, {"url": "https://one.com/x", "headline": "Yancoal lifts output",
                                "published": "2026-08-02T00:00:00Z"}, "gdelt", "high")
    b = archive.make_record(e, {"url": "https://two.com/y", "headline": "Yancoal lifts output!",
                                "published": "2026-08-01T00:00:00Z"}, "gnews", "high")
    merged, _ = archive.merge([], [a, b])
    assert len(merged) == 1, merged
    assert merged[0]["published"] == "2026-08-02T00:00:00Z", "should keep the newer one"


@check("the rolling cap is honoured exactly")
def _t24():
    records = [{"id": str(i), "ticker": "A", "published": f"2026-08-{(i % 28) + 1:02d}T00:00:00Z"}
               for i in range(500)]
    out = archive.apply_window(records, cap=100, floor=5)
    assert len(out) == 100, len(out)


@check("a loud company cannot evict a quiet one below its floor")
def _t25():
    loud = [{"id": f"L{i}", "ticker": "LOUD",
             "published": f"2026-08-20T00:{i % 60:02d}:00Z"} for i in range(400)]
    quiet = [{"id": f"Q{i}", "ticker": "QUIET",
              "published": f"2026-07-0{i + 1}T00:00:00Z"} for i in range(4)]
    out = archive.apply_window(loud + quiet, cap=100, floor=10)
    assert len(out) == 100
    kept_quiet = [r for r in out if r["ticker"] == "QUIET"]
    assert len(kept_quiet) == 4, (
        f"all 4 QUIET records should survive under a floor of 10, kept {len(kept_quiet)}")


@check("archive writes atomically and reloads identically")
def _t26():
    e = entity()
    rec = archive.make_record(e, {"url": "https://ex.com/a", "headline": "H",
                                  "published": "2026-08-01T00:00:00Z"}, "gdelt", "high")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "articles.json")
        archive.save(path, [rec], entities=archive.entity_manifest([e]))
        assert archive.load(path) == [rec]
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["count"] == 1
        assert payload["entities"][0]["ticker"] == "T"
        assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]


@check("a corrupt archive is refused rather than silently discarded")
def _t27():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "articles.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        try:
            archive.load(path)
            raise AssertionError("expected ArchiveError")
        except archive.ArchiveError:
            pass


@check("a second concurrent run cannot write the archive")
def _t28():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "articles.json")
        with archive.Lock(path):
            try:
                with archive.Lock(path):
                    raise AssertionError("second lock should not have been granted")
            except archive.ArchiveError:
                pass
        with archive.Lock(path):        # released cleanly, so this succeeds
            pass


# ---------------------------------------------------------------- CI behaviour

@check("an unchanged article set produces no commit-worthy diff")
def _t30():
    e = entity()
    rec = archive.make_record(e, {"url": "https://ex.com/a", "headline": "H",
                                  "published": "2026-08-01T00:00:00Z"}, "gdelt", "high")
    other = archive.make_record(e, {"url": "https://ex.com/b", "headline": "H2",
                                    "published": "2026-08-02T00:00:00Z"}, "gdelt", "high")
    # Order must not matter, so a re-sorted archive is not a "change".
    assert archive.signature([rec, other]) == archive.signature([other, rec])
    assert archive.signature([rec]) != archive.signature([rec, other])
    assert archive.signature([]) == archive.signature([])


@check("the generated timestamp alone never counts as a change")
def _t31():
    import time as _time
    e = entity()
    rec = archive.make_record(e, {"url": "https://ex.com/a", "headline": "H",
                                  "published": "2026-08-01T00:00:00Z"}, "gdelt", "high")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "data", "articles.json")
        archive.save(path, [rec], entities=[])
        first = archive.load(path)
        _time.sleep(1.05)                       # force a different timestamp
        archive.save(path, [rec], entities=[])
        second = archive.load(path)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert archive.signature(first) == archive.signature(second), \
            "a rewrite with identical articles must not register as a change"
        assert payload["signature"] == archive.signature([rec])


@check("a purge-only run still counts as a change and gets written")
def _t31b():
    # The bug this pins: the change fingerprint was compared against the
    # post-revalidation set rather than the file on disk, so a run that only
    # purged foreign articles reported "no change" and never wrote them out.
    e = entity()
    a = archive.make_record(e, {"url": "https://ex.com/a", "headline": "H1",
                                "published": "2026-08-01T00:00:00Z"}, "gdelt", "high")
    b = archive.make_record(e, {"url": "https://ex.com/b", "headline": "H2",
                                "published": "2026-08-02T00:00:00Z"}, "gdelt", "high")
    stored = [a, b]
    after_purge = [b]                      # `a` failed revalidation
    final = archive.apply_window(after_purge)
    assert archive.signature(stored) != archive.signature(final), \
        "a purge must register as a change"
    assert archive.signature(after_purge) == archive.signature(final), \
        "comparing post-purge to final would hide the purge"


@check("the circuit breaker trips a dead source and spares the rest of the run")
def _t32():
    from tools import collect as col
    breaker = col.Breaker({"gdelt", "gnews"})
    for _ in range(col.BREAKER_THRESHOLD):
        breaker.fail("gdelt")
    assert not breaker.live("gdelt"), "gdelt should have tripped"
    assert breaker.live("gnews"), "gnews must be unaffected"
    assert breaker.errors["gdelt"] == col.BREAKER_THRESHOLD
    # A success before the threshold resets the streak.
    fresh = col.Breaker({"gnews"})
    for _ in range(col.BREAKER_THRESHOLD - 1):
        fresh.fail("gnews")
    fresh.ok("gnews")
    fresh.fail("gnews")
    assert fresh.live("gnews"), "an intervening success must reset the streak"


@check("collector writes machine-readable outcome for the workflow")
def _t33():
    from tools import collect as col
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "gh_output")
        summary = os.path.join(tmp, "gh_summary")
        os.environ["GITHUB_OUTPUT"] = out
        os.environ["GITHUB_STEP_SUMMARY"] = summary
        try:
            col.emit_outputs(changed="true", count=12, new=3, status="ok")
            col.emit_summary(["## Report", "", "| a | b |"])
        finally:
            os.environ.pop("GITHUB_OUTPUT", None)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
        lines = open(out, encoding="utf-8").read().splitlines()
        assert "changed=true" in lines, lines
        assert "new=3" in lines and "status=ok" in lines, lines
        assert "## Report" in open(summary, encoding="utf-8").read()
    # Outside CI the calls must be silent no-ops, not crashes.
    col.emit_outputs(changed="false")
    col.emit_summary(["x"])


# ---------------------------------------------------------------- end to end

@check("a full offline run attributes a real headline to the right company")
def _t29():
    loaded = {e.ticker: e for e in ent.load(ENTITY_FILE)}
    tokyo = loaded["TKYO SL Equity"]
    peabody = loaded["BTU US Equity"]

    feed = json.dumps({"articles": [
        {"url": "https://ex.lk/a", "title": "Tokyo Cement reports higher quarterly profit",
         "seendate": "20260812T101500Z", "domain": "ex.lk"},
        {"url": "https://ex.com/b", "title": "Peabody Award winners announced",
         "seendate": "20260812T090000Z", "domain": "ex.com"},
    ]}).encode()

    def fake_fetch(url, accept="*/*"):
        return feed

    hits = gdelt.fetch_articles(tokyo, dt.date(2026, 8, 1), dt.date(2026, 8, 31),
                                fetcher=fake_fetch)
    scored = [(matching.score(tokyo, h["headline"])[0], h["headline"]) for h in hits]
    assert (matching.HIGH, "Tokyo Cement reports higher quarterly profit") in scored
    # The awards story must not be attributed to Tokyo Cement at all.
    assert all(v == matching.REJECT for v, h in scored if "Award" in h)

    # For Peabody the awards collision is a *known* one, so the resolver names
    # it as a negative term and the story is rejected outright, not merely
    # flagged. That is the stronger outcome and the one we want to lock in.
    verdict, reason = matching.score(peabody, "Peabody Award winners announced")
    assert verdict == matching.REJECT, (verdict, reason)
    assert "negative term" in reason, reason

    # The distinctive form settles it on its own, with no context needed.
    verdict, reason = matching.score(peabody, "Peabody Energy raises output guidance")
    assert verdict == matching.HIGH, (verdict, reason)
    assert "weak" not in reason, reason

    # The bare form is weak: corroborated it is confirmed, uncorroborated it
    # is flagged for the analyst rather than dropped or asserted.
    assert matching.score(peabody, "Peabody lifts coal output")[0] == matching.HIGH
    verdict, _ = matching.score(peabody, "Peabody named in a civic dispute")
    assert verdict == matching.LOW, verdict


def main() -> int:
    print("running portfolio news monitor tests\n")
    for name in PASSED:
        print(f"  PASS  {name}")
    for name, why in FAILED:
        print(f"  FAIL  {name}\n          {why}")
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
