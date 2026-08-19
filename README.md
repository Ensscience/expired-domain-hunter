# Expired Domain Hunter

Expired Domain Hunter is a standalone Python utility for automatically collecting, filtering, scoring, ranking, and reporting the **TOP 50 expired or dropped `.COM` domains**. Availability is a separate RDAP-enriched property: `AVAILABLE`, `REGISTERED`, `PENDING`, `AUCTION`, and `UNKNOWN` are all explicitly labeled, and `UNKNOWN` domains may remain in the TOP 50. It uses multiple public feeds and keeps a user-provided CSV as a manual fallback.

The project runs independently through GitHub Actions. Manus is not part of the runtime, and the application does not use a Manus API, Manus account, Manus credential, paid API, paid server, paid database, or paid hosting service.

> **Important limitation:** Scores and resale ranges are heuristic research aids. They are not appraisals, guarantees of availability, legal opinions, or guarantees that a domain can be resold at the indicated price. Always verify registrar status, auction status, historical content, backlinks, trademark risk, and potential buyers manually.

## What the system does

On each run, the collector downloads multiple public expired/dropped feeds, keeps only expired or dropped lifecycle records, excludes pending-delete, auction, backorder, expiring, pre-release, bidding, buy-now, and aftermarket records, filters to `.COM`, deduplicates, and writes a normalized `input/domains.csv`. The hunter applies cheap local quality/spam filters, calculates the existing transparent 100-point score for all qualifying candidates, ranks them deterministically, selects the TOP 50, enriches those candidates with bounded RDAP and appropriate Wayback checks, and writes `output/results.csv`, `output/top_domains.txt`, `output/source_report.csv`, and JSON summaries. One consolidated TOP 50 Telegram report is sent only when the dataset fingerprint is genuinely new.

The Wayback history step uses the public CDX endpoint only for shortlisted candidates, applies a request budget, uses timeouts and retries, and does not attempt to bypass CAPTCHAs, logins, anti-bot controls, rate limits, or access restrictions. The public CDX interface and archive browsing are documented by the Internet Archive [Wayback CDX Server project](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) and [Wayback Machine](https://web.archive.org/).

## Architecture

| Component | Responsibility |
|---|---|
| `hunter.py` | Command-line entry point and end-to-end orchestration |
| `config.py` | Thresholds, weights, network limits, and bid guardrails |
| `collector.py` | Command-line automatic feed collector and normalized-input writer |
| `src/collector.py` | Multiple public raw-feed downloads, lifecycle exclusion, `.COM` filtering, deduplication, provenance, dataset fingerprints, and source report |
| `src/availability.py` | Conservative Verisign `.COM` RDAP status checks |
| `src/state.py` | Persistent domain status and one-report-per-dataset notification state |
| `src/data_source.py` | CSV import, column aliases, normalization, and invalid-row handling |
| `src/filters.py` | `.COM` filtering, active-status rejection, prohibited-term and spam signals |
| `src/history.py` | Bounded Wayback CDX checks and historical-use signals |
| `src/scoring.py` | Transparent initial/final scoring, end-user categories, and conservative estimates |
| `src/telegram.py` | Consolidated TOP 50 dataset reports, safe message splitting, and integration-test message |
| `input/domains.csv` | Normalized automatic collector output or manual CSV fallback |
| `output/collection_summary.json` | Feed counts, dataset ID/date, hashes, duplicates, errors, and fallback status |
| `output/source_report.csv` | Per-source metadata, ETag, Last-Modified, date, hash, and counts |
| `output/results.csv` | Machine-readable ranked TOP 50 with availability status |
| `output/top_domains.txt` | Human-readable TOP 50 report |
| `.github/workflows/hunt.yml` | Daily and manual GitHub Actions execution |

The primary feeds are the public [WhoisFreaks daily expired-and-dropped GitHub feed](https://github.com/WhoisFreaks/daily-expired-and-dropped-domains): [`0-latest-free-expired-domains.csv`](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv) and [`0-latest-free-dropped-domains.csv`](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv). The secondary feed is the public [UniqueDomains expired-oneword extract](https://github.com/UniqueDomains/expired-oneword-domains), downloaded from [`expired.csv`](https://raw.githubusercontent.com/UniqueDomains/expired-oneword-domains/main/expired.csv). All three are public raw files; the collector does not scrape an interactive marketplace.

The WhoisFreaks files are newline-delimited and described by their publisher as daily free subsets. The UniqueDomains file is a daily public extract of expired one-word domains, but only 1,000 rows and not the full catalog. All records are normalized, filtered to `.COM`, and tagged with source provenance. A formal source and compliance comparison is maintained in [`docs/data_sources.md`](docs/data_sources.md). If the public feeds fail, the collector does not synthesize data: it preserves the existing `input/domains.csv` fallback and records `fallback_used` in `output/collection_summary.json`.

## TOP 50 quality and availability pipeline

The runtime pipeline is intentionally ordered as follows:

> Public expired/dropped sources → dataset fingerprint → expired/dropped-only lifecycle filter → `.COM` filter → deduplication → cheap local quality/spam filters → initial scoring → rank all qualifying domains → select TOP 50 → RDAP enrichment → Wayback/history where appropriate → final score → Telegram.

The collector rejects records labeled pending, pending delete, redemption, auction, backorder, expiring, pre-release, bidding, buy now, or aftermarket. The hunter then applies cheap local quality and spam filters and calculates the existing 100-point score for every qualifying candidate with neutral history. Candidates are ranked by quality score, commercial intent, keyword quality, brandability, end-user potential, shorter length, and deterministic domain-name tie-breakers. The TOP 50 are selected by Hunter quality—not by RDAP status and not by input order. Verisign RDAP enriches those candidates only; a valid RDAP domain object is `REGISTERED`, a valid authoritative RDAP 404 error object is `AVAILABLE`, RDAP lifecycle/hold signals are `PENDING`, source lifecycle markers are `AUCTION`, and timeouts, malformed responses, rate limits, access restrictions, or other inconclusive results are `UNKNOWN`. UNKNOWN domains remain eligible for the TOP 50 and are never described as available. AVAILABLE entries say: **AVAILABLE — verify at registrar before registration.**

RDAP availability is a point-in-time registry signal, not a guarantee that a registrar checkout will succeed. Names can be registered between the check and registration, and registry-reserved or registrar-specific restrictions may exist. Every output and Telegram report therefore states **QUALITY SCORE ≠ AVAILABILITY**, labels every entry, and keeps UNKNOWN separate from AVAILABLE.

The workflow runs three times per day at **08:00 UTC**, **14:00 UTC**, and **20:00 UTC**, and also supports manual `workflow_dispatch`. `.state/processed_domains.json` is persisted through the GitHub Actions cache. Dataset fingerprints combine source identity, feed URL, content SHA-256, ETag, and Last-Modified metadata; a dataset already reported is not sent again. Scheduled runs therefore detect a new feed snapshot at the next poll, while manual runs can process it immediately.

## Installation and local execution

Python 3.11 or newer is recommended.

```bash
git clone https://github.com/Ensscience/expired-domain-hunter.git
cd expired-domain-hunter
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python hunter.py
```

For a fast offline/sample run that does not contact the Wayback Machine:

```bash
python hunter.py --skip-wayback
```

For a run with an explicit request budget:

```bash
python hunter.py --max-wayback 25
```

The commands create `input/domains.csv`, `output/collection_summary.json`, `output/source_report.csv`, `output/results.csv`, `output/top_domains.txt`, and `output/run_summary.json`. An empty or missing fallback CSV is handled gracefully; the collector never fabricates domain records.

To run automatic collection locally:

```bash
python collector.py
python hunter.py
```

## CSV input format

The required semantic field is `domain`. All other fields are optional. The importer recognizes common aliases and ignores unknown columns, so the file can be adapted to different legitimate data providers.

| Semantic column | Examples | How it is used |
|---|---|---|
| `domain` | `smartinvoices.com` | Required; only syntactically valid `.com` names are accepted |
| `status` | `expired`, `dropped`, `auction` | Explicit active/registered names are rejected |
| `backlinks` | `420` | Backlink-volume signal |
| `ref_domains` | `58` | Referring-domain quality proxy |
| `domain_age` | `12` | Age signal in years when supplied |
| `archive_year` | `2012` | Historical-age signal when supplied |
| `keyword` | `invoicing software` | Keyword quality and end-user inference |
| `search_volume` | `5400` | Commercial-demand proxy when supplied |
| `source` | `provider-a` | Primary provenance carried into results |
| `source_count` | `2` | Number of independent source providers for the row |
| `sources` | `whoisfreaks-public-github;uniquedomains-public-extract` | Full source provenance list |

A minimal file is valid:

```csv
domain
smartinvoices.com
cloudledger.com
```

The manual import procedure remains available: obtain a CSV from a source that permits automated access or download, map its domain field to `domain`, retain any useful optional metrics, save it as `input/domains.csv`, and run the hunter. Do not scrape or automate a service that explicitly prohibits bots, crawlers, or AI agents.

## Filtering and risk signals

The system prioritizes one-word and two-word names, short readable labels, natural English words, commercial keywords, software and technology, finance, ecommerce, marketing, AI, SaaS, and business services. It penalizes numbers, hyphens, awkward spelling, difficult pronunciation, excessive length, obvious spam, prohibited/high-risk terms, and possible trademark terms.

Trademark detection is **not complete**. A possible trademark match is only a risk signal and must be checked manually. The prohibited-term filter is also intentionally conservative and should be reviewed before any acquisition decision.

## Scoring system

The positive score is transparent and totals 100 points before penalties.

| Component | Maximum points |
|---|---:|
| Brandability | 20 |
| Commercial intent | 20 |
| Keyword quality | 15 |
| Length and readability | 10 |
| Historical quality | 10 |
| Backlink/referring-domain quality | 10 |
| Age and history | 5 |
| End-user potential | 10 |
| **Total** | **100** |

The system then applies bounded penalties for numbers, hyphens, awkward spelling, suspicious history, spam signals, possible trademark risk, and weak commercial potential. The classification thresholds are:

| Score | Classification |
|---:|---|
| 80–100 | BUY CANDIDATE |
| 65–79 | WATCH |
| 0–64 | IGNORE |

End-user potential is intentionally separate from SEO metrics. For strong candidates, the system infers likely industries and business types from the name and keyword fields and records why a real company might want the domain. This is a prioritization aid, not a prediction of buyer demand.

## Resale estimate and maximum bid

For each ranked candidate, the program writes an estimated resale range and a conservative suggested maximum bid. The bid is deliberately capped and is based on the lower portion of the heuristic resale range rather than the optimistic upper bound. The TOP 50 Telegram summary and human-readable report include:

> **AI estimate — manual verification required.**

The system is intended to surface opportunities where possible resale value is materially greater than a conservative acquisition limit. It does not know registrar fees, auction competition, renewal fees, negotiations, buyer-specific budgets, exact trademark status, or whether a name is actually available.

## Telegram configuration

The workflow reads these GitHub Actions secrets and never hard-codes them:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The Telegram Bot API is called only from the workflow or from an explicitly requested local command. For every genuinely new dataset, the system sends one consolidated **TOP 50 EXPIRED .COM** report, split into multiple Telegram messages only when the message-length limit requires it. Each entry includes its rank, quality score, availability status, source provenance, and reason. The report explicitly states **QUALITY SCORE ≠ AVAILABILITY**. `AVAILABLE` means verify at a registrar before registration; `UNKNOWN` is never described as available. The API endpoint and message method follow the public [Telegram Bot API documentation](https://core.telegram.org/bots/api).

To test delivery manually from GitHub Actions, open the **Expired Domain Hunter** workflow, choose **Run workflow**, set **telegram_test** to `true`, and start it. This sends one clearly labeled integration-test message and then runs the normal hunt. The test option is opt-in and does not expose secrets in logs.

## GitHub Actions

The workflow in `.github/workflows/hunt.yml`:

1. Runs at `08:00 UTC`, `14:00 UTC`, and `20:00 UTC`.
2. Supports `workflow_dispatch` for manual execution.
3. Restores and saves persistent dataset/domain state through the GitHub Actions cache.
4. Installs Python dependencies.
5. Downloads and normalizes the latest public expired and dropped feeds.
6. Records ETag, Last-Modified, dataset date, content hashes, and source counts.
7. Scores and ranks all locally qualifying domains, selects the TOP 50, and enriches those candidates with bounded RDAP/Wayback checks.
8. Sends one TOP 50 Telegram report only when the dataset fingerprint is new.
9. Uploads `output/` as a GitHub Actions artifact retained for 14 days.

The workflow uses GitHub-hosted runners and the standard [GitHub Actions workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions). The application itself has no persistent database requirement. If historical state is needed later, the output artifact or a committed user-managed data file can be used without adding a paid service.

To run it manually with the normal pipeline, select **Run workflow** and leave the optional inputs disabled. The collector obtains real public feed data, computes the dataset fingerprint, scores and ranks all qualifying domains, selects the TOP 50, and then performs bounded RDAP enrichment on those candidates. `skip_wayback` skips only history checks; it does not skip RDAP enrichment. `availability_budget` controls how many TOP 50 entries receive RDAP checks; it does not reduce the TOP 50 report itself. `reset_state` is reserved for explicit fresh verification and should not be used for normal scheduled operation.

## Testing

The repository includes dependency-light unit tests using Python’s standard library:

```bash
python -m unittest discover -s tests -v
```

The tests cover public-feed parsing, secondary-feed parsing, lifecycle exclusion, retry and failure handling, `.COM` filtering, deduplication, ETag/Last-Modified/hash metadata, RDAP AVAILABLE/REGISTERED/PENDING/UNKNOWN outcomes, TOP 50 selection without an RDAP hard gate, persistent domain and dataset state, invalid domains, prohibited and spam signals, active-status rejection, scoring, classification, conservative bid calculation, empty datasets, CSV output headers, bounded Wayback parsing, Telegram TOP 50 formatting and message splitting. A local automatic collection and hunt run is:

```bash
python collector.py
python hunter.py --skip-wayback
cat output/collection_summary.json
cat output/results.csv
cat output/top_domains.txt
```

The tests use fake HTTP sessions and do not send a Telegram message or make Wayback requests.

## Troubleshooting

| Symptom | Resolution |
|---|---|
| `Automatic collection failed` | Check the public raw-feed URLs and GitHub connectivity. The collector preserves the manual fallback only when all public feeds fail. |
| `No eligible .COM domains` | Check that `input/domains.csv` has a `domain` column and valid `.com` names. |
| `TOP 50 report not sent` | Inspect `output/collection_summary.json` and persistent state. The dataset fingerprint may already be marked as reported, or Telegram credentials/delivery may have failed. |
| Telegram summary not sent | Confirm both secret names are exact, the bot can message the target chat, and the dataset fingerprint is genuinely new. |
| Wayback errors | Use `--skip-wayback` for an offline run; the pipeline remains usable and records the Wayback URL for manual review. |
| Scores seem too low | Review the name, optional keyword/search-volume data, risk signals, and the transparent weights in `config.py`. |
| No daily data arrives | Inspect `output/collection_summary.json` and `output/source_report.csv`. The free sources are partial subsets; the workflow polls at 08:00, 14:00, and 20:00 UTC, so expected detection delay is up to the next scheduled poll, approximately 0–6 hours after a source update. |
| Workflow does not appear | Confirm the workflow file is on the repository’s default branch and that Actions are enabled for the repository. |

## Independence audit

The runtime imports only Python standard-library modules plus `requests`, which is installed from `requirements.txt`. It downloads three public raw source files, records response metadata and content hashes, performs public Verisign `.COM` RDAP GET checks on TOP 50 entries, optionally calls public Wayback and Telegram HTTPS endpoints, and writes local output files. It uses GitHub Actions cache for persistent dataset and domain state and does not reference Manus packages, Manus environment variables, Manus APIs, Manus credentials, or another repository. The GitHub Actions workflow is sufficient to execute the project after the current development session ends.

## License and use

This repository is intended for personal research and automation. Before buying or marketing any domain, independently verify availability, registration history, backlink quality, content history, trademarks, regulatory issues, and the identity and needs of potential end users.
