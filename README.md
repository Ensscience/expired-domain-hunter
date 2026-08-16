# Expired Domain Hunter

Expired Domain Hunter is a standalone Python utility for automatically collecting and ranking potentially valuable expired or dropped **.COM** domains. It uses a public daily feed as the primary input and keeps a user-provided CSV as a manual fallback. It is designed to prioritize a small number of plausible end-user opportunities rather than produce an unranked list of thousands of names.

The project runs independently through GitHub Actions. Manus is not part of the runtime, and the application does not use a Manus API, Manus account, Manus credential, paid API, paid server, paid database, or paid hosting service.

> **Important limitation:** Scores and resale ranges are heuristic research aids. They are not appraisals, guarantees of availability, legal opinions, or guarantees that a domain can be resold at the indicated price. Always verify registrar status, auction status, historical content, backlinks, trademark risk, and potential buyers manually.

## What the system does

On each run, the collector downloads the latest public expired and dropped lists, combines them, filters to `.com`, deduplicates domains, and writes a normalized `input/domains.csv`. The hunter then accepts valid `.com` domains, rejects obvious prohibited or spam-like candidates, performs bounded Wayback CDX checks on eligible candidates, calculates a transparent 100-point score, classifies each result, estimates a conservative maximum bid, and writes ranked output files under `output/`. When at least one domain scores 80 or higher, it sends one daily Telegram summary rather than one message per domain.

The Wayback history step uses the public CDX endpoint only for shortlisted candidates, applies a request budget, uses timeouts and retries, and does not attempt to bypass CAPTCHAs, logins, anti-bot controls, rate limits, or access restrictions. The public CDX interface and archive browsing are documented by the Internet Archive [Wayback CDX Server project](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) and [Wayback Machine](https://web.archive.org/).

## Architecture

| Component | Responsibility |
|---|---|
| `hunter.py` | Command-line entry point and end-to-end orchestration |
| `config.py` | Thresholds, weights, network limits, and bid guardrails |
| `collector.py` | Command-line automatic feed collector and normalized-input writer |
| `src/collector.py` | Public raw-feed download, `.COM` filtering, deduplication, retry limits, and collection summary |
| `src/data_source.py` | CSV import, column aliases, normalization, and invalid-row handling |
| `src/filters.py` | `.COM` filtering, active-status rejection, prohibited-term and spam signals |
| `src/history.py` | Bounded Wayback CDX checks and historical-use signals |
| `src/scoring.py` | Transparent scoring, end-user categories, and conservative estimates |
| `src/telegram.py` | One daily summary and an explicit integration-test message |
| `input/domains.csv` | Normalized automatic collector output or manual CSV fallback |
| `output/collection_summary.json` | Feed counts, `.COM` counts, duplicates, errors, and fallback status |
| `output/results.csv` | Machine-readable ranked results |
| `output/top_domains.txt` | Human-readable strongest opportunities |
| `.github/workflows/hunt.yml` | Daily and manual GitHub Actions execution |

The automatic source is the public [WhoisFreaks daily expired-and-dropped GitHub feed](https://github.com/WhoisFreaks/daily-expired-and-dropped-domains). Its two raw files are directly downloadable without login: [`0-latest-free-expired-domains.csv`](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv) and [`0-latest-free-dropped-domains.csv`](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv). The files are newline-delimited domain lists, so `src/collector.py` normalizes them into the existing CSV schema. The collector makes one request per feed, uses a 30-second timeout and two retries, adds a descriptive User-Agent, keeps only `.com`, and does not scrape an interactive marketplace.

This is the strongest legitimate free option found for unattended execution because it is a public, machine-readable, date-updated feed with no login or CAPTCHA requirement. It is not a complete registry-wide dataset: the publisher describes the free feed as a 10,000-domain-per-file subset, with partial gTLD coverage and a stated one-day delay; the larger commercial product is separate. The feed is also not `.COM`-only, so local filtering is required. If both public files fail, the collector does not synthesize data: it preserves the existing `input/domains.csv` fallback and records `fallback_used` in `output/collection_summary.json`. A formal source comparison is maintained in [`docs/data_sources.md`](docs/data_sources.md).

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

The commands create `input/domains.csv`, `output/collection_summary.json`, `output/results.csv`, `output/top_domains.txt`, and `output/run_summary.json`. An empty or missing fallback CSV is handled gracefully; the collector never fabricates domain records.

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
| `source` | `provider-a` | Provenance carried into results |

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

For each candidate, the program writes an estimated resale range and a conservative suggested maximum bid. The bid is deliberately capped and is based on the lower portion of the heuristic resale range rather than the optimistic upper bound. Every qualifying Telegram summary and human-readable report includes:

> **AI estimate — manual verification required.**

The system is intended to surface opportunities where possible resale value is materially greater than a conservative acquisition limit. It does not know registrar fees, auction competition, renewal fees, negotiations, buyer-specific budgets, exact trademark status, or whether a name is actually available.

## Telegram configuration

The workflow reads these GitHub Actions secrets and never hard-codes them:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

The Telegram Bot API is called only from the workflow or from an explicitly requested local command. The production alert rule is strict: only domains scoring **80+** can produce an alert, and all qualifying domains are consolidated into one daily summary. The API endpoint and message method follow the public [Telegram Bot API documentation](https://core.telegram.org/bots/api).

To test delivery manually from GitHub Actions, open the **Expired Domain Hunter** workflow, choose **Run workflow**, set **telegram_test** to `true`, and start it. This sends one clearly labeled integration-test message and then runs the normal hunt. The test option is opt-in and does not expose secrets in logs.

## GitHub Actions

The workflow in `.github/workflows/hunt.yml`:

1. Runs every day at `08:15 UTC`.
2. Supports `workflow_dispatch` for manual execution.
3. Installs Python dependencies.
4. Downloads and normalizes the latest public expired and dropped feeds.
5. Optionally sends a Telegram integration-test message.
6. Runs the unchanged hunter with the configured secrets.
7. Uploads `output/` as a GitHub Actions artifact retained for 14 days.

The workflow uses GitHub-hosted runners and the standard [GitHub Actions workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions). The application itself has no persistent database requirement. If historical state is needed later, the output artifact or a committed user-managed data file can be used without adding a paid service.

To run it manually with the normal pipeline, select **Run workflow** and leave both optional inputs disabled. The collector will obtain real public feed data before the hunter starts. To perform a quick offline/sample execution, set **skip_wayback** to `true`; automatic collection still runs. Scheduled runs use normal Wayback checks subject to the request budget.

## Testing

The repository includes dependency-light unit tests using Python’s standard library:

```bash
python -m unittest discover -s tests -v
```

The tests cover public-feed parsing, retry and failure handling, `.COM` filtering, deduplication, invalid domains, prohibited and spam signals, active-status rejection, scoring, classification, conservative bid calculation, empty datasets, CSV output headers, bounded Wayback parsing, Telegram summary formatting, and the one-summary alert rule. A local automatic collection and hunt run is:

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
| `No eligible .COM domains` | Check that `input/domains.csv` has a `domain` column and valid `.com` names. |
| Telegram summary not sent | Confirm both secret names are exact, the bot can message the target chat, and at least one score is 80+. |
| Wayback errors | Use `--skip-wayback` for an offline run; the pipeline remains usable and records the Wayback URL for manual review. |
| Scores seem too low | Review the name, optional keyword/search-volume data, risk signals, and the transparent weights in `config.py`. |
| No daily data arrives | Inspect `output/collection_summary.json`. The free source is a partial subset and may have a one-day delay; if both raw feeds are unavailable, the checked-in CSV fallback is preserved and the summary marks `fallback_used`. |
| Workflow does not appear | Confirm the workflow file is on the repository’s default branch and that Actions are enabled for the repository. |

## Independence audit

The runtime imports only Python standard-library modules plus `requests`, which is installed from `requirements.txt`. It downloads two public GitHub raw files, reads or preserves local CSV input, optionally calls public Wayback and Telegram HTTPS endpoints, and writes local output files. It does not reference Manus packages, Manus environment variables, Manus APIs, Manus credentials, or another repository. The GitHub Actions workflow is sufficient to execute the project after the current development session ends.

## License and use

This repository is intended for personal research and automation. Before buying or marketing any domain, independently verify availability, registration history, backlink quality, content history, trademarks, regulatory issues, and the identity and needs of potential end users.
