# Automatic expired-domain data sources

## Selected source

The collector uses the public [WhoisFreaks daily expired-and-dropped-domains GitHub repository](https://github.com/WhoisFreaks/daily-expired-and-dropped-domains). It downloads these two raw files directly:

- [Latest free expired domains](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv)
- [Latest free dropped domains](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv)

The repository README describes separate expired and dropped feeds, daily updates, public machine-readable access, a free 10,000-domain-per-file subset, a stated one-day delay, partial gTLD coverage, and no ccTLD coverage in the free feed. The raw files observed on 2026-08-16 were newline-delimited domain names rather than CSV files with headers. The collector normalizes them into the existing `domain,status,source` schema.

This source was selected because it has the strongest combination of freshness, public direct download, machine-readable format, no login requirement, no CAPTCHA step, and no need to scrape an interactive marketplace. The workflow makes one request per feed per run, which is modest for a once-daily schedule.

## Live measurement at implementation time

The two raw files were downloaded successfully on 2026-08-16.

| Measurement | Expired feed | Dropped feed | Combined before deduplication |
|---|---:|---:|---:|
| Raw lines | 10,000 | 10,000 | 20,000 |
| Valid `.COM` lines | 5,139 | 4,507 | 9,646 |

The combined `.COM` count is not necessarily the final unique count because a domain can appear in both feeds. The collector reports both per-feed counts and the deduplicated count in `output/collection_summary.json` on every run.

## Alternatives considered

| Source | Observed capability | Decision |
|---|---|---|
| WhoisFreaks public GitHub feed | Direct raw files, separate expired/dropped lists, daily public snapshots, no login for download | **Selected** |
| [ExpiredDomains.net](https://www.expireddomains.net/) | Large interactive research site with filters and visible `.com` pending-delete lists; no clearly documented public raw feed or API was found on the reviewed public page | Not used for unattended collection; avoid unapproved scraping |
| [Expired-domains.com](https://www.expired-domains.com/) | Free interactive browsing and CSV export advertised, but the reviewed page describes account-based access for extended date ranges and larger exports and exposes no stable public raw-feed URL or documented API | Manual fallback only unless explicit feed/API permission is obtained |

The project does not automate a source merely because a browser can display it. It avoids CAPTCHA, login restrictions, robots restrictions, anti-bot systems, rate-limit bypasses, and access-control workarounds.

## Limitations

The selected free feed is not a complete daily dump of all expired or dropped `.COM` domains. The publisher describes it as a partial free subset, with a stated one-day delay and incomplete TLD coverage. It does not provide the scoring engine’s optional SEO metrics, search volume, domain age, or referring-domain data in the raw files, so those fields are empty unless a later legitimate enrichment step supplies them. The hunter therefore relies primarily on name quality, commercial signals, and its bounded Wayback history checks for these automatically collected candidates.

If both raw downloads fail, the collector does not simulate or invent domains. It preserves the existing checked-in `input/domains.csv` and marks `fallback_used: true` in `output/collection_summary.json`. If the fallback is absent or empty, the workflow fails clearly instead of pretending that automatic collection succeeded.

## Compliance posture

The collector uses ordinary HTTPS GET requests to public raw GitHub URLs with a descriptive User-Agent, a 30-second timeout, and two retries. It runs at most once per feed per workflow run. It does not access authenticated pages, submit forms, defeat CAPTCHAs, bypass robots or rate limits, or scrape the interactive pages of marketplace sites.

## References

1. [WhoisFreaks daily expired-and-dropped-domains repository](https://github.com/WhoisFreaks/daily-expired-and-dropped-domains)
2. [WhoisFreaks free expired-domain raw feed](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv)
3. [WhoisFreaks free dropped-domain raw feed](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv)
4. [ExpiredDomains.net](https://www.expireddomains.net/)
5. [Expired-domains.com](https://www.expired-domains.com/)
