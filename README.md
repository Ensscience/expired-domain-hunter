# Expired Domain Hunter

Expired Domain Hunter is a standalone Python utility for finding and ranking potentially valuable expired or dropped **.COM** domains from a user-provided CSV. It is designed to prioritize a small number of plausible end-user opportunities rather than produce an unranked list of thousands of names.

The project runs independently through GitHub Actions. Manus is not part of the runtime, and the application does not use a Manus API, Manus account, Manus credential, paid API, paid server, paid database, or paid hosting service.

> **Important limitation:** Scores and resale ranges are heuristic research aids. They are not appraisals, guarantees of availability, legal opinions, or guarantees that a domain can be resold at the indicated price. Always verify registrar status, auction status, historical content, backlinks, trademark risk, and potential buyers manually.

## What the system does

On each run, the hunter reads `input/domains.csv`, accepts valid `.com` domains, rejects obvious prohibited or spam-like candidates, performs bounded Wayback CDX checks on eligible candidates, calculates a transparent 100-point score, classifies each result, estimates a conservative maximum bid, and writes ranked output files under `output/`. When at least one domain scores 80 or higher, it sends one daily Telegram summary rather than one message per domain.

The Wayback history step uses the public CDX endpoint only for shortlisted candidates, applies a request budget, uses timeouts and retries, and does not attempt to bypass CAPTCHAs, logins, anti-bot controls, rate limits, or access restrictions. The public CDX interface and archive browsing are documented by the Internet Archive [Wayback CDX Server project](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) and [Wayback Machine](https://web.archive.org/).

## Architecture

| Component | Responsibility |
|---|---|
| `hunter.py` | Command-line entry point and end-to-end orchestration |
| `config.py` | Thresholds, weights, network limits, and bid guardrails |
| `src/data_source.py` | CSV import, column aliases, normalization, and invalid-row handling |
| `src/filters.py` | `.COM` filtering, active-status rejection, prohibited-term and spam signals |
| `src/history.py` | Bounded Wayback CDX checks and historical-use signals |
| `src/scoring.py` | Transparent scoring, end-user categories, and conservative estimates |
| `src/telegram.py` | One daily summary and an explicit integration-test message |
| `input/domains.csv` | User-provided daily candidate input |
| `output/results.csv` | Machine-readable ranked results |
| `output/top_domains.txt` | Human-readable strongest opportunities |
| `.github/workflows/hunt.yml` | Daily and manual GitHub Actions execution |

The data collector is intentionally modular. If a legitimate free source provides a daily CSV, place or generate that file as `input/domains.csv`. If no suitable automated source is available, the hunter remains fully operational with a manual CSV import. The project does not pretend that a blocked or bot-prohibited marketplace source is available.

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

The command creates `output/results.csv`, `output/top_domains.txt`, and `output/run_summary.json`. An empty or missing CSV is handled gracefully and still produces the required output headers.

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

A daily import procedure is therefore: obtain a CSV from a source that permits automated access or download, map its domain field to `domain`, retain any useful optional metrics, save it as `input/domains.csv`, and run the workflow manually or wait for the daily schedule. Do not scrape or automate a service that explicitly prohibits bots, crawlers, or AI agents.

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
4. Optionally sends a Telegram integration-test message.
5. Runs the hunter with the configured secrets.
6. Uploads `output/` as a GitHub Actions artifact retained for 14 days.

The workflow uses GitHub-hosted runners and the standard [GitHub Actions workflow syntax](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions). The application itself has no persistent database requirement. If historical state is needed later, the output artifact or a committed user-managed data file can be used without adding a paid service.

To run it manually with the normal pipeline, select **Run workflow** and leave both optional inputs disabled. To perform a quick offline/sample execution, set **skip_wayback** to `true`. Scheduled runs use normal Wayback checks subject to the request budget.

## Testing

The repository includes dependency-light unit tests using Python’s standard library:

```bash
python -m unittest discover -s tests -v
```

The tests cover `.COM` filtering, invalid domains, prohibited and spam signals, active-status rejection, scoring, classification, conservative bid calculation, empty datasets, CSV output headers, bounded Wayback parsing, Telegram summary formatting, and the one-summary alert rule. A local sample run is:

```bash
python hunter.py --skip-wayback
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
| No daily data arrives | GitHub Actions cannot invent a legitimate domain feed. Import a permitted CSV into `input/domains.csv`; the workflow will process it. |
| Workflow does not appear | Confirm the workflow file is on the repository’s default branch and that Actions are enabled for the repository. |

## Independence audit

The runtime imports only Python standard-library modules plus `requests`, which is installed from `requirements.txt`. It reads local CSV input, optionally calls public Wayback and Telegram HTTPS endpoints, and writes local output files. It does not reference Manus packages, Manus environment variables, Manus APIs, Manus credentials, or another repository. The GitHub Actions workflow is sufficient to execute the project after the current development session ends.

## License and use

This repository is intended for personal research and automation. Before buying or marketing any domain, independently verify availability, registration history, backlink quality, content history, trademarks, regulatory issues, and the identity and needs of potential end users.
