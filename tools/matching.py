"""Decide whether a headline is really about a given company.

Only the headline (plus the publisher name, where a feed supplies one) is
inspected — the project never fetches article bodies. That is a real
constraint on what can be known, so the rules are deliberately conservative
and the outcome is graded rather than binary:

    reject  no alias appeared, a scoped row's required term was absent, or a
            weak alias collided with a hard block term
    low     ...also when a flag term appeared, or when a hard block term
            appeared alongside an exact company name
    high    an alias appeared and either the name is distinctive, or an
            ambiguous name was corroborated by a context term
    low     an ambiguous name appeared with nothing to corroborate it, or the
            row's scope was satisfied only by the publisher rather than the
            headline itself

Rows carrying required_terms - the macro themes - are scoped: "inflation" only
counts when a Sri Lanka marker is also present. A marker found in the headline
is proof; one found only in the publisher name is a hint, and downgrades the
result to low rather than settling it.

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

    hit = entity.matched_alias(text)
    if not hit:
        return REJECT, "no alias in headline"
    alias, is_weak = hit

    negative = entity.hits_negative(haystack)

    # Precedence: a STRONG alias outranks a hard block. If the headline names
    # the holding exactly - "Blackstone Inc and Blackstone Minerals both named
    # in nickel report" - the article is about the holding, whatever else it
    # also mentions. Deleting it there would lose real coverage. A weak alias
    # gets no such privilege: a bare "Blackstone" beside "Blackstone Inc" is
    # far more likely to be the other one.
    if is_weak and negative:
        return REJECT, f"negative term '{negative}'"

    flag = entity.hits_flag(haystack)

    # A strong alias settles it on its own, and on a macro row it is also its
    # own proof of scope: "AWPLR" and "CCPI" are Sri Lankan by construction, so
    # they need no separate marker and must be tested BEFORE the scope gate.
    if not is_weak:
        if negative:
            return LOW, f"alias '{alias}' but negative term '{negative}' also present"
        if flag:
            return LOW, f"alias '{alias}', flagged term '{flag}'"
        return HIGH, f"alias '{alias}'"

    # From here the alias is weak, so the row's scope must carry it. Absent
    # altogether, the article is not ours at all.
    if not entity.has_required(haystack):
        return REJECT, "weak alias but required scope term absent"

    # A weak alias on a scoped row needs the marker in the headline itself.
    # Finding it only in the outlet's name is suggestive, not conclusive — a
    # Sri Lankan paper also reports on the rest of the world — so the analyst
    # is asked to confirm rather than told.
    if entity.required_terms and not entity.has_required(text):
        return LOW, f"weak alias '{alias}', scope only from publisher"

    if flag:
        return LOW, f"weak alias '{alias}', flagged term '{flag}'"

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
