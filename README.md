# Portfolio News Monitor

Headlines and links for the 74 companies in the portfolio **and six Sri Lankan
macroeconomic themes**, collected daily from free structured feeds and served as
a static dashboard. **No article text is
ever fetched or stored** — every result is a headline plus a link to the
publisher.

```
data/entities.csv          <- the resolver: names, aliases, context, exclusions
        │
        ├── tools/collect.py    queries GDELT + Google News, scores each match,
        │                       writes docs/data/articles.json  (GitHub Actions, daily)
        │
        └── docs/index.html     reads that JSON in the browser and filters it
                                (GitHub Pages — static, no server, nothing uploaded)
```

## Why a script and not an agent

The collector is deterministic: the same feeds and the same resolver produce the
same archive, every time. An LLM browsing for headlines would return different
results on two runs of the same query, cost money per run, and give no auditable
reason why an article was included. For a research desk that is the wrong trade.
Language models are useful here *offline* — for drafting and refining the alias
lists — and that output is committed as data in `data/entities.csv`.

## The resolver is the whole ballgame

Everything downstream is mechanical. Accuracy lives in one file, and it is
generated from a readable table rather than hand-edited as CSV:

```bash
python -m tools.build_entities            # regenerate data/entities.csv
python -m tools.build_entities --check    # validate, write nothing
python -m tools.build_entities --workbook Ken_s_Updated_Portfolio_.xlsx
```

Edit `ROWS` (companies) or `MACRO_ROWS` (economic themes) in
`tools/build_entities.py`, never the CSV directly — **the CSV is build output**.
After any edit run `python -m tools.build_entities` and commit the regenerated
`data/entities.csv` alongside your code change. If you forget, CI regenerates it
for that run and posts a warning rather than failing. Each row carries:

| field | what it does |
|---|---|
| `aliases` | every name the company is known by, including former ones. A leading `~` marks a *weak* alias — see below |
| `context_terms` | words that corroborate a weak alias |
| `negative_terms` | **hard block** — the article is deleted |
| `flag_terms` | **soft block** — the article is kept but graded `low` |
| `ambiguous` | `yes` when at least one alias is weak; validated against the `~` markers |
| `status` | `active` / `renamed` / `acquired` / `sanctioned` / `delisted` |
| `note` | shown to the analyst on hover in the dashboard |
| `verified` | date the corporate status was last checked, or blank |
| `kind` | `company` (an issuer) or `macro` (an economic theme) |
| `required_terms` | at least one must appear or the article is rejected — how macro rows are scoped |

**Strong and weak aliases.** Ambiguity belongs to a name, not to a company.
"Peabody Energy" settles the question on its own; a bare "Peabody" does not.
So each alias carries its own strength, and weak ones are written with a
leading `~`:

```
Peabody Energy|Peabody Coal|~Peabody
```

A strong alias scores `high` unaided. A weak alias needs a context term, and
without one the article is flagged rather than asserted or discarded. Strong
aliases are tried first, so a headline containing both forms is settled by the
strong one. Dropping the weak form entirely is the tempting shortcut and the
wrong one — most headlines say "Peabody swings to profit", so you would lose
most of the real coverage to avoid a little noise.

Validation is fail-closed: one malformed row rejects the entire file rather than
quietly collecting against a broken alias set. An `ambiguous` row with no context
terms is refused, and so is any row whose `ambiguous` flag disagrees with its
`~` markers — the summary column can never drift from what the matcher does.

### Two tiers of exclusion

A block you get wrong is invisible: it removes articles nobody ever sees. So
exclusions come in two strengths, and anything arguable belongs in the weaker
one.

- **`negative_terms` (hard).** Only for terms that mean a plainly different
  subject — the Peabody Awards, the Cavendish banana, the Stanmore Tube station,
  Morgan Stanley. The article is deleted.
- **`flag_terms` (soft).** For words that *usually* mean the wrong subject but
  sometimes do not. "Private equity" beside Blackstone Minerals is normally the
  other Blackstone — yet a miner can genuinely raise money from one. The article
  is kept and graded `low`, so the judgement sits with the analyst.

**An exact company name outranks a hard block.** A headline reading "Blackstone
Inc and Blackstone Minerals both named in nickel report" names the holding
outright, so deleting it would lose real coverage. It is kept and graded `low`.
A *weak* alias gets no such privilege — a bare "Blackstone" beside "Blackstone
Inc" is almost certainly the other one, and is dropped.

Both lists live in the `BLOCK` and `FLAG` tables in `build_entities.py`, applied
as an overlay so the main tables stay readable. 23 rows carry hard blocks, 16
carry soft flags; the rest have names distinctive enough that inventing terms
would add risk with no gain.

Validation refuses a term that is identical to one of the row's own aliases
(it would delete everything the alias was meant to find) and a term listed in
both tiers at once.

**Scope is an allowlist, not a filter.** This is the central design decision
for macro rows, and it was learned the hard way. The first version searched the
world's news and filtered out what looked foreign — a blocklist, which fails
open: anything not explicitly excluded gets through, so foreign coverage kept
reappearing however many terms were added.

A macro article is now accepted only if it can be **proven** Sri Lankan:

1. **GDELT restricts by publishing country.** Macro queries carry
   `sourcecountry:CE`, enforced by the API rather than by getting a keyword
   right.
2. **The publisher must be on the allowlist** (`tools/sources_allowlist.py`):
   any `.lk` domain, plus a short verified list of Sri Lankan outlets on
   generic TLDs. Matching is on the registrable domain, so subdomains work and
   `dailymirror.lk.evil.com` does not.
3. **Or the headline itself names the country.** Then any publisher is fine —
   that is how a Reuters piece on Sri Lanka's IMF programme still gets in.

Foreign publisher *and* no marker in the headline means rejected, whatever the
feed returned.

**Google News is not used for macro rows.** Its search does not honour a
boolean scope block, and its links are `news.google.com` redirects that hide
the real publisher, so it can satisfy neither test above. Company rows keep it:
a name like "Yancoal" carries its own scope.

The trade-off, stated plainly: macro coverage from foreign outlets is lost
unless the headline names Sri Lanka. In practice it usually does.

**Scope terms must be unambiguous, and their order matters.** The query builder
takes the first few terms from `required_terms`, so the strongest markers go
first. Two things are deliberately excluded:

- **Outlet names.** An early version used "Daily Mirror", "Sunday Times",
  "Sunday Observer" and "The Island" to prove Sri Lankan scope. Those are all UK
  papers, so the feeds were being asked for British news and returned the
  world's economics coverage. Sri Lankan outlets are now recognised by their
  **.lk domain** instead, which cannot be confused with anything.
- **"rupee".** India, Pakistan, Nepal, Mauritius and the Seychelles have one too.

`build_entities` audits this and **fails the build** if any macro row's generic
terms would be queried without a Sri Lanka marker attached — that failure is
otherwise silent, since the archive simply fills with foreign news.

**Weak aliases are always narrowed at the source.** A bare query for "Peabody"
or "inflation" returns the whole world and leaves the matcher to discard almost
all of it — wasteful, and it burns the per-run caps on junk. Every weak alias is
ANDed with something: `required_terms` on a macro row, `context_terms` on a
company. Strong aliases stay unscoped, since narrowing them would only lose
coverage that was already unambiguous.

**Macro rows exclude non-economic Sri Lankan news.** Cricket is the single
largest category of Sri Lankan coverage, so all six macro rows block cricket,
horoscopes, lotteries, obituaries, film and weather, and flag opinion columns.
Regional neighbours are deliberately *not* blocked: "India's exports to Sri
Lanka rise" is real news for this desk.

### How a match is graded

Only the headline (plus the publisher name, where a feed gives one) is
inspected. That is a real limit on what can be known, so the verdict is graded
rather than binary:

- **reject** — no alias appeared, a scoped row's required term was absent, or a
  weak alias collided with a hard block
- **high** — a strong alias appeared, or a weak one was corroborated by context
- **low** — a weak alias appeared with nothing to corroborate it, a flag term
  fired, a hard block appeared alongside an exact company name, or a macro row's
  scope came only from the publisher

Low-confidence matches are kept and flagged in the dashboard as *needs a look*,
never silently mixed in with the certain ones and never silently dropped.
13 of the 74 companies carry at least one weak alias.

### Macro rows

Six rows track Sri Lankan economic news by segment — **Monetary, Fiscal,
External, Real, Financial, Commodities** — across about 170 terms.

They work differently from company rows in one respect. "Inflation" and "budget
deficit" describe every economy on earth, so a macro row carries
`required_terms` and matches **nothing** unless a Sri Lanka marker is present
too. That scope block is ANDed into the feed query itself, so the search asks
for Sri Lankan inflation news rather than downloading the world's and throwing
most of it away.

Three rules follow from this, and each has a test:

- **Strong macro aliases are self-scoping.** `AWPLR`, `CCPI`, `SDFR` and `CBSL`
  are Sri Lankan by construction. They confirm on their own, are tested *before*
  the scope gate, and are queried *without* the scope block — narrowing them
  would only lose coverage that was already unambiguous.
- **Weak terms need the marker in the headline.** `~inflation`, `~exchange rate`,
  `~budget deficit` only count when "Sri Lanka", "Colombo", "CBSL" or "rupee"
  appears alongside.
- **Scope from the publisher alone is a hint, not proof.** A headline reading
  "Inflation eases to 3.2%" in a Sri Lankan outlet is graded `low` and flagged,
  because a Sri Lankan paper also reports on the rest of the world.

Because six themes over 170 terms out-produce all 74 companies combined, macro
records are capped at **40% of the archive** (`MACRO_SHARE_OF_ARCHIVE`) and get
their own higher per-run ceiling. The dashboard's **Coverage** filter switches
between *Holdings* and *Sri Lanka economy*.

Adding terms is a one-line edit to `MACRO_ROWS` in `tools/build_entities.py`,
then a rebuild. `build_entities` reports the resulting request count, and warns
if a row has grown past what the query planner can fit — chunks beyond
`MAX_QUERY_CHUNKS` are dropped, so that warning matters.

### Corporate actions

The universe sheet dates from July 2022 and several names have moved since. The
rule is: **track what the company is now**, keep the old names as aliases so
historical coverage still matches, and surface the change as a note.

Verified against sources on 2026-08-24:

| Was | Now |
|---|---|
| MACA (MLD AU) | acquired by Thiess, control Oct 2022 — Thiess is private, so news flow is thin |
| Laredo Petroleum (LPI US) | renamed Vital Energy Jan 2023, then acquired by Crescent Energy |
| Mastermyne (MYE AU) | renamed Metarock 2022, reverted to Mastermyne Nov 2024 |
| Cenkos (CNKS LN) | merged with finnCap Sept 2023 → Cavendish Financial (AIM: CAV) |
| Texhong Textile (2678 HK) | renamed Texhong International Group Feb 2023, code unchanged |
| "Taftnet" (ATAD LI) | a mis-spelling of **Tatneft**; ATAD was the London ADR line |

The four Russian lines (Gazprom, Lukoil, Bashneft, Tatneft) are retained and
tracked although effectively uninvestable; they carry `status: sanctioned` and
are badged in the dashboard.

**65 of the 74 rows have a blank `verified` field** — their corporate status is
carried from the July 2022 workbook and has not been independently checked. That
is a known gap, not an oversight. Work through them by searching each name,
correcting `ROWS`, and setting `verified` to the date you checked.

## Running the collector

```bash
pip install -r requirements.txt

python -m tools.collect --dry-run          # fetch, report, write nothing
python -m tools.collect --backfill         # first run: back to 1 July 2026
python -m tools.collect                    # normal daily run (3-day lookback)
python -m tools.collect --only "743 HK Equity" --sources gdelt   # debugging
```

## Running on GitHub Actions

`.github/workflows/collect.yml` runs the collector daily at **00:30 UTC = 06:00
Asia/Colombo**. Sri Lanka has no daylight saving, so that holds year-round.
Nothing needs configuring: `GITHUB_TOKEN` is provided automatically and the job
requests `contents: write` and nothing else.

Trigger it by hand from the **Actions** tab — *Collect portfolio news* → *Run
workflow*. Two switches are offered there: **backfill** (search back to 1 July
2026, for the first run) and **dry run** (fetch and report, write nothing).

The job in order: check out, install, rebuild and validate the resolver, run the
tests, collect, commit only if something changed. The two gates run **before**
anything touches the network — a broken alias file or a failing test should stop
the run, not write a bad archive.

The resolver step *rebuilds* `data/entities.csv` rather than only checking it.
The CSV is generated from `build_entities.py`, so a code change that adds a
column would otherwise fail every run until someone remembered to regenerate and
commit. Rebuilding removes that failure mode entirely; a stale committed file
produces a warning on the run summary, not a red build.

### What makes it survive an unattended runner

**A quiet day produces no commit.** The archive carries a `generated` timestamp
that changes every run, so a plain file diff is always dirty and CI would commit
daily whether or not it found anything. Instead the collector fingerprints the
article set and writes only when that fingerprint moves, publishing
`changed=true|false` to `GITHUB_OUTPUT`; the commit step is gated on it. Your
history then means something: one commit per day on which the news actually
changed.

**A source outage costs one round of retries, not seventy-four.** Each source
has a circuit breaker. Five consecutive failures and it is skipped for the rest
of the run. Without it, a GDELT outage would burn 74 companies × 3 retries ×
a 20-second timeout — comfortably past the job timeout, for nothing. If *every*
source trips, the job exits **1** so the run goes red and you find out, rather
than the archive quietly going stale for a fortnight.

**The run finishes rather than being killed.** `--budget-minutes 30` sits under
the 45-minute job timeout, so on a slow day the collector stops fetching and
writes what it has. `SIGTERM` and `SIGINT` are caught and turned into an orderly
finish, so a cancelled run still saves its work.

**Two runs never collide.** A `concurrency` group serialises them with
`cancel-in-progress: false`, so a manual backfill is queued behind the nightly
cron instead of killing it. If a push is rejected anyway, the step rebases and
retries up to three times.

**You can see what happened without reading logs.** Every run writes a summary
table to the Actions run page: window, companies processed, headlines seen,
confirmed vs flagged, new after dedupe, archive size, source errors.

### Exit codes

| code | meaning |
|---|---|
| 0 | ran to completion, or stopped on budget, with usable results |
| 1 | every source failed — nothing collected, archive untouched |
| 2 | configuration error: bad arguments or a rejected entity file |

### One thing to watch

GitHub disables scheduled workflows in a repository with no activity for 60
days. If the only commits are the bot's own, that clock can run down during a
quiet stretch. You will get an email first; re-enable from the Actions tab.

## The archive heals itself

Every run re-scores the stored records against the *current* rules and drops
those that no longer qualify, before merging anything new.

Without this the archive is append-only, so anything collected under a rule
that later proves wrong sits on the dashboard until the rolling cap evicts it —
and a correction appears to do nothing for weeks. Re-scoring makes fixes
retroactive: change a term, and the next run cleans out everything it should
never have kept. Records whose entity no longer exists are dropped too, and
grades are refreshed, so an article that used to confirm but is now only
flagged stops claiming more certainty than the rules support.

Revalidation is skipped on a `--only` run, which would otherwise purge every
row it was not asked to look at.

## The archive is a working set, not a record

By design, this keeps recent headlines rather than accumulating history:

- at most **3,000** records, oldest evicted as newer arrive
- each row retains its most recent **10** records before the global cut
- macro records are held to **40%** of the window

That floor matters. Under pure oldest-first eviction a heavily-covered name
would push out every headline about a quiet one — and the quiet frontier names
are exactly where an analyst needs the help. A record is one (company, article)
pair, so a story matching two companies is two records.

At 40–80 articles a day the cap binds quickly; expect an effective horizon of
roughly six weeks once it fills. If you later want history, raise
`MAX_ARCHIVE_RECORDS` in `tools/schema.py` or shard by month — the collector
does not otherwise care.

## Security

**The collector**

- **SSRF allowlist.** Only `api.gdeltproject.org` and `news.google.com` are ever
  contacted. Redirects are not auto-followed; every hop is re-validated and the
  hop count capped. Hosts resolving to private, loopback, link-local or reserved
  addresses are refused, which also blunts DNS rebinding of an allowlisted name.
- **No HTML, ever.** Nothing parses a web page or fetches an article body. The
  attack surface is two structured formats.
- **XXE-safe XML.** RSS is untrusted input, so it is parsed with `defusedxml`;
  the stdlib parsers are vulnerable to external-entity and entity-expansion
  attacks. Both are covered by tests.
- **Bounded everything.** Response size cap, timeout, retry limit, item cap,
  headline and URL length caps, per-company per-run cap.
- **Fail-soft parsing.** A malformed or hostile payload yields an empty result
  rather than an exception that would abort the run.
- **Atomic writes.** Timestamped backup, exclusive lock, temp-then-replace — an
  interrupted run cannot leave a truncated archive staged for commit.
- **Least-privilege CI.** The workflow holds `contents: write` and nothing else.

**The dashboard**

- **No third-party code.** No CDN, no external font, no analytics — which lets
  the CSP be `default-src 'self'` with `object-src 'none'`, `base-uri 'none'`,
  `form-action 'none'`, `frame-ancestors 'none'` and no `unsafe-inline`.
- **Headlines are untrusted.** They come from the open web and reach the page
  only through `createElement` and `.textContent`, never `innerHTML`. A headline
  containing markup is *displayed*, not parsed.
- **Links are scheme-checked twice** — once when the record is built, once
  before the `href` is set — so a `javascript:` or `data:` URL arriving inside a
  feed can never reach the DOM. Outbound links carry
  `rel="noopener noreferrer nofollow"`.
- **Read-only.** The site fetches one JSON file from its own origin. There is no
  upload path, no form, no backend and no secret.

## Deploy the dashboard

Settings → **Pages** → Source: *Deploy from a branch* → Branch `main`, folder
`/docs` → Save. `.nojekyll` is included so the dashboard is served rather than
the README. Preview locally:

```bash
cd docs && python -m http.server 8000     # then open http://localhost:8000
```

Opening `index.html` directly off the filesystem will not work — `fetch` needs an
origin. The dashboard says so if it happens.

## Tests

```bash
python tests/test_all.py
```

60 tests, no network required. They cover entity-file validation, every branch
of the match grading, strong-over-weak alias precedence, accent folding, word-boundary and punctuation matching,
regex-metacharacter safety, the SSRF allowlist, `javascript:`/`data:` link
rejection, XXE and billion-laughs payloads, both feed parsers, URL
canonicalisation, dedupe, the rolling cap, the per-company floor, atomic writes,
corrupt-archive handling, lock contention, change-signature stability, the source
circuit breaker, the Actions output contract, macro scope gating in both the
matcher and the query builders, the macro share ceiling, both exclusion tiers,
the precedence of an exact name over a hard block, and two regressions: that
every scoped query carries a Sri Lanka marker, and that no scope term is a name
another country's press also uses. They also cover the publisher allowlist, the
source-country restriction, archive revalidation, and that a purge-only run is
correctly detected as a change.
