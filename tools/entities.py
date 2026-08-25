"""Load and validate data/entities.csv into matcher-ready Entity objects.

The entity file is the one place a human encodes knowledge the collector
cannot infer: what a company is really called, what it used to be called,
which words prove an article is about it, and which words prove it is not.
Everything downstream is mechanical, so this file is where accuracy lives.

Validation is fail-closed: one malformed row rejects the whole file rather
than silently collecting against a broken alias set.
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field

from . import schema


class EntityError(Exception):
    pass


def _split(cell: str) -> list[str]:
    """Pipe-separated cell -> de-duplicated, order-preserving list."""
    out, seen = [], set()
    for part in (cell or "").split("|"):
        part = part.strip()
        if part and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return out


def _term_pattern(term: str) -> re.Pattern:
    """Word-boundary matcher for one alias or keyword.

    Built with re.escape so a term is always literal text — an entity file is
    trusted-but-human-edited, and we do not want a stray '(' to become a regex
    or a '.*' to quietly match everything.

    \\b fails next to non-word characters, so terms that start or end with
    punctuation ("Thai O.P.P.", "King's Flair") get a lookaround instead.
    """
    esc = re.escape(term)
    left = r"\b" if term[0].isalnum() else r"(?<!\w)"
    right = r"\b" if term[-1].isalnum() else r"(?!\w)"
    return re.compile(left + esc + right, re.IGNORECASE)


@dataclass
class Entity:
    """A tracked company and the vocabulary that identifies it.

    Aliases come in two strengths. A *strong* alias is distinctive enough to
    settle the question on its own ("Peabody Energy", "Stanmore Resources").
    A *weak* alias, written with a leading '~' in the CSV, is a form that
    could refer to something else ("~Peabody", "~Stanmore") and therefore
    needs a context term before a match counts as confirmed.
    """
    ticker: str
    name: str
    aliases: list[str]
    context_terms: list[str]
    negative_terms: list[str]
    required_terms: list[str]
    kind: str
    country: str
    industry: str
    ambiguous: bool
    status: str
    note: str
    verified: str
    weak: set[str] = field(default_factory=set)
    _alias_res: list[re.Pattern] = field(default_factory=list, repr=False)
    _context_res: list[re.Pattern] = field(default_factory=list, repr=False)
    _negative_res: list[re.Pattern] = field(default_factory=list, repr=False)
    _required_res: list[re.Pattern] = field(default_factory=list, repr=False)

    def compile(self) -> "Entity":
        self._alias_res = [_term_pattern(a) for a in self.aliases]
        self._context_res = [_term_pattern(t) for t in self.context_terms]
        self._negative_res = [_term_pattern(t) for t in self.negative_terms]
        self._required_res = [_term_pattern(t) for t in self.required_terms]
        return self

    @property
    def is_macro(self) -> bool:
        return self.kind == "macro"

    def has_required(self, text: str) -> bool:
        """True when the scope condition is met, or when there isn't one."""
        if not self._required_res:
            return True
        return any(rx.search(text) for rx in self._required_res)

    def matched_alias(self, text: str) -> tuple[str, bool] | None:
        """Return (alias, is_weak) for the first alias found, else None.

        Strong aliases are tried first so that a headline containing both
        "Peabody Energy" and a bare "Peabody" is settled by the strong form.
        """
        pairs = list(zip(self.aliases, self._alias_res))
        for alias, rx in sorted(pairs, key=lambda p: p[0] in self.weak):
            if rx.search(text):
                return alias, alias in self.weak
        return None

    def has_context(self, text: str) -> bool:
        return any(rx.search(text) for rx in self._context_res)

    def hits_negative(self, text: str) -> str | None:
        for term, rx in zip(self.negative_terms, self._negative_res):
            if rx.search(text):
                return term
        return None


def _row_errors(row: dict, seen_tickers: set[str]) -> list[str]:
    errs: list[str] = []

    for col in schema.ENTITY_REQUIRED:
        if not (row.get(col) or "").strip():
            errs.append(f"{col} is required")
    if errs:
        return errs

    ticker = row["ticker"].strip()
    if ticker in seen_tickers:
        errs.append(f"duplicate ticker '{ticker}'")

    status = row["status"].strip().lower()
    if status not in schema.STATUSES:
        errs.append(f"status '{status}' not in {sorted(schema.STATUSES)}")

    kind = row["kind"].strip().lower()
    if kind not in schema.KINDS:
        errs.append(f"kind '{kind}' not in {sorted(schema.KINDS)}")

    ambiguous = row["ambiguous"].strip().lower()
    if ambiguous not in schema.YES_NO:
        errs.append(f"ambiguous '{ambiguous}' must be yes or no")

    aliases = [a.lstrip("~") for a in _split(row.get("aliases", ""))]
    weak = [a for a in _split(row.get("aliases", "")) if a.startswith("~")]
    if not aliases:
        errs.append("at least one alias is required")
    for a in aliases:
        if len(a) < 3:
            errs.append(f"alias '{a}' is too short to match safely (min 3 chars)")
        # Headlines are accent-folded before matching, so an alias carrying a
        # diacritic ("Tofaş", "Çimsa") could never match. Store the folded
        # form; it matches both the accented and unaccented headline.
        from .matching import normalise as _fold
        if _fold(a) != a:
            errs.append(f"alias '{a}' must be stored accent-folded as '{_fold(a)}'")

    # An ambiguous name without corroborating terms would flood the archive
    # with false positives, so refuse the row rather than accept the noise.
    if ambiguous == "yes" and not _split(row.get("context_terms", "")):
        errs.append("ambiguous entities must supply at least one context term")

    # The 'ambiguous' flag and the '~' markers must agree, so the summary
    # column can never drift away from what the matcher actually does.
    if ambiguous == "yes" and not weak:
        errs.append("ambiguous='yes' but no alias is marked weak with a leading '~'")
    if ambiguous == "no" and weak:
        errs.append(f"alias(es) marked weak {weak} but ambiguous='no'")

    # A macro row's aliases ("inflation", "budget deficit") would otherwise
    # match every economics story on earth. Refuse the row outright rather than
    # let an unscoped theme flood the archive.
    required = _split(row.get("required_terms", ""))
    if kind == "macro" and not required:
        errs.append("macro rows must supply required_terms to scope them")
    for t in required:
        if len(t) < 3:
            errs.append(f"required term '{t}' is too short to scope safely")
        from .matching import normalise as _fold2
        if _fold2(t) != t:
            errs.append(f"required term '{t}' must be stored accent-folded")

    verified = (row.get("verified") or "").strip()
    if verified and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified):
        errs.append(f"verified '{verified}' must be blank or YYYY-MM-DD")

    return errs


def load(path: str) -> list[Entity]:
    """Parse and validate the entity file. Raises EntityError on any problem."""
    if not os.path.isfile(path):
        raise EntityError(f"entity file not found: {path}")

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        missing = [c for c in schema.ENTITY_COLS if c not in cols]
        extra = [c for c in cols if c not in schema.ENTITY_COLS]
        if missing:
            raise EntityError(f"entity file missing columns: {missing}")
        if extra:
            raise EntityError(f"entity file has unexpected columns: {extra}")
        rows = list(reader)

    if not rows:
        raise EntityError("entity file has no rows.")

    entities: list[Entity] = []
    problems: list[str] = []
    seen: set[str] = set()

    for i, row in enumerate(rows, start=2):          # +2: header + 1-indexed
        errs = _row_errors(row, seen)
        if errs:
            problems.append(f"row {i}: " + "; ".join(errs))
            continue
        seen.add(row["ticker"].strip())
        entities.append(Entity(
            ticker=row["ticker"].strip(),
            name=row["name"].strip(),
            aliases=[a.lstrip("~") for a in _split(row["aliases"])],
            weak={a.lstrip("~") for a in _split(row["aliases"]) if a.startswith("~")},
            context_terms=_split(row.get("context_terms", "")),
            negative_terms=_split(row.get("negative_terms", "")),
            required_terms=_split(row.get("required_terms", "")),
            kind=row["kind"].strip().lower(),
            country=(row.get("country") or "").strip(),
            industry=(row.get("industry") or "").strip(),
            ambiguous=row["ambiguous"].strip().lower() == "yes",
            status=row["status"].strip().lower(),
            note=(row.get("note") or "").strip(),
            verified=(row.get("verified") or "").strip(),
        ).compile())

    if problems:
        raise EntityError(
            f"{len(problems)} invalid entity row(s):\n  " + "\n  ".join(problems[:10])
        )
    return entities
