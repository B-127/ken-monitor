"""Decide whether a headline is really about a given company.

Only the headline (plus the publisher name, where a feed supplies one) is
inspected — the project never fetches article bodies. That is a real
constraint on what can be known, so the rules are deliberately conservative
and the outcome is graded rather than binary:

    reject  a negative term fired, or no alias appeared at all
    high    an alias appeared and either the name is distinctive, or an
            ambiguous name was corroborated by a context term
    low     an ambiguous name appeared with nothing to corroborate it

Low-confidence matches are kept and flagged, not discarded — the analyst
filters them in the dashboard. The point is that they are never silently
mixed in with the certain ones.
"""
from __future__ import annotations

import re
import unicodedata

from .entities import Entity

# Collapse accents so "Tofaş" matches "Tofas" and "Çimsa" matches "Cimsa".
# Turkish, Vietnamese and Indonesian coverage all depend on this.
def normalise(text: str) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Normalise quote and dash variants that differ between feeds.
    stripped = (stripped.replace("\u2019", "'").replace("\u2018", "'")
                        .replace("\u201c", '"').replace("\u201d", '"')
                        .replace("\u2013", "-").replace("\u2014", "-"))
    return re.sub(r"\s+", " ", stripped).strip()


REJECT, LOW, HIGH = "reject", "low", "high"


def score(entity: Entity, headline: str, publisher: str = "") -> tuple[str, str]:
    """Return (verdict, reason). Verdict is one of reject / low / high."""
    text = normalise(headline)
    if not text:
        return REJECT, "empty headline"

    # Publisher name is corroborating context, never grounds for a match on
    # its own — otherwise every article from a Sri Lankan outlet would look
    # like a match for every Sri Lankan company.
    haystack = f"{text} {normalise(publisher)}".strip()

    negative = entity.hits_negative(haystack)
    if negative:
        return REJECT, f"negative term '{negative}'"

    hit = entity.matched_alias(text)
    if not hit:
        return REJECT, "no alias in headline"
    alias, is_weak = hit

    # A distinctive alias settles it on its own.
    if not is_weak:
        return HIGH, f"alias '{alias}'"

    if entity.has_context(haystack):
        return HIGH, f"weak alias '{alias}' + context"

    return LOW, f"weak alias '{alias}', no corroboration"


def canonical_url(url: str) -> str:
    """Strip tracking parameters so the same article from two feeds dedupes.

    Conservative on purpose: only well-known tracking keys are removed, and
    the rest of the query is preserved, because many regional outlets carry
    the article id in a query parameter.
    """
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    drop_prefixes = ("utm_",)
    drop_exact = {
        "fbclid", "gclid", "igshid", "mc_cid", "mc_eid",
        "ref", "ref_src", "cmpid", "smid", "spm", "at_medium", "at_campaign",
    }
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(drop_prefixes) and k.lower() not in drop_exact
    ]
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(kept), ""))


def dedupe_title(headline: str) -> str:
    """Key for spotting the same story syndicated under near-identical titles."""
    text = normalise(headline).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
