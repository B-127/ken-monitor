"""Sri Lankan news publishers, as an allowlist.

Scope used to be a blocklist: search the world, then filter out what looked
foreign. Blocklists fail open - anything not explicitly excluded gets through,
which is why foreign coverage kept appearing however many terms were added.

This inverts it. A macro article is accepted only if it can be PROVEN Sri
Lankan: either it came from a publisher on this list, or its headline says so
outright. Anything else is rejected, whatever the feed returned.

Matching is on the registrable domain, so subdomains and www forms all work,
and a lookalike domain ("dailymirror.lk.evil.com") does not.

Adding an outlet: put the bare domain in. Do not add a bare '.com' domain
unless the publication really is Sri Lankan - that is the one way to reopen
the hole this file closes.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# Any .lk domain is Sri Lankan by registration, so it needs no listing.
_LK_TLD = re.compile(r"(?:^|\.)lk$", re.I)

# Sri Lankan outlets that publish on a generic TLD and so cannot be caught by
# the rule above. Keep this list short and verified.
NON_LK_TLD_PUBLISHERS = frozenset({
    "economynext.com",
    "colombopage.com",
    "colombogazette.com",
    "dailymirror.lk",          # listed for clarity; .lk rule already covers it
    "newswire.lk",
    "themorning.lk",
    "sundaytimes.lk",
    "island.lk",
    "ft.lk",
    "adaderana.lk",
    "newsfirst.lk",
    "dailynews.lk",
    "bizenglish.adaderana.lk",
    "lankabusinessonline.com",
    "sundayobserver.lk",
    "ceylontoday.lk",
    "dailyft.lk",
})


def registrable_domain(value: str) -> str:
    """Reduce a URL or host string to a bare lowercase host.

    GDELT gives a bare domain; a URL is accepted too so callers need not care.
    """
    if not value:
        return ""
    value = value.strip().lower()
    if "://" in value:
        value = urlsplit(value).netloc or ""
    value = value.split("@")[-1].split(":")[0]
    return value[4:] if value.startswith("www.") else value


def is_sri_lankan(domain_or_url: str) -> bool:
    """True when the publisher is verifiably Sri Lankan."""
    host = registrable_domain(domain_or_url)
    if not host:
        return False
    if _LK_TLD.search(host):
        return True
    if host in NON_LK_TLD_PUBLISHERS:
        return True
    # Allow subdomains of a listed publisher, but never a domain that merely
    # ends with one as a substring.
    return any(host.endswith("." + pub) for pub in NON_LK_TLD_PUBLISHERS)
