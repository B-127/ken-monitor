# Ken Portfolio Monitor

Headlines and links for the 74 companies in the portfolio, collected daily from
free structured feeds and served as a static dashboard. **No article text is
ever fetched or stored** every result is a headline plus a link to the
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

Edit `ROWS` in `tools/build_entities.py`, never the CSV directly — the CSV is
build output. Each row carries:

| field | what it does |
|---|---|
| `aliases` | every name the company is known by, including former ones. A leading `~` marks a *weak* alias — see below |
| `context_terms` | words that corroborate a weak alias |
| `negative_terms` | words that disprove a match — a collision we know about |
| `ambiguous` | `yes` when at least one alias is weak; validated against the `~` markers |
| `status` | `active` / `renamed` / `acquired` / `sanctioned` / `delisted` |
| `note` | shown to the analyst on hover in the dashboard |
| `verified` | date the corporate status was last checked, or blank |

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

### How a match is graded

Only the headline (plus the publisher name, where a feed gives one) is
inspected. That is a real limit on what can be known, so the verdict is graded
rather than binary:

- **reject** — a negative term fired, or no alias appeared at all
- **high** — a strong alias appeared, or a weak one was corroborated by context
- **low** — a weak alias appeared with nothing to corroborate it

Low-confidence matches are kept and flagged in the dashboard as *needs a look*,
never silently mixed in with the certain ones and never silently dropped.
13 of the 74 companies carry at least one weak alias.

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

The job in order: check out, install, validate the resolver, run the tests,
collect, commit only if something changed. The two gates run **before** anything
touches the network — a broken alias file or a failing test should stop the run,
not write a bad archive.

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

## The archive is a working set, not a record

By design, this keeps recent headlines rather than accumulating history:

- at most **3,000** records, oldest evicted as newer arrive
- but each company retains its most recent **10** records before the global cut

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

35 tests, no network required. They cover entity-file validation, every branch
of the match grading, strong-over-weak alias precedence, accent folding, word-boundary and punctuation matching,
regex-metacharacter safety, the SSRF allowlist, `javascript:`/`data:` link
rejection, XXE and billion-laughs payloads, both feed parsers, URL
canonicalisation, dedupe, the rolling cap, the per-company floor, atomic writes,
corrupt-archive handling, lock contention, change-signature stability, the source
circuit breaker and the Actions output contract.
