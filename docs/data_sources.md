# Automatic expired-domain sources and hand-registration verification

## Sources actually used

The collector uses three public raw feeds:

| Feed | Lifecycle | Format | Role |
|---|---|---|---|
| [WhoisFreaks latest free expired list](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv) | Expired | One domain per line | Primary fresh feed |
| [WhoisFreaks latest free dropped list](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv) | Dropped | One domain per line | Primary fresh feed |
| [UniqueDomains expired extract](https://raw.githubusercontent.com/UniqueDomains/expired-oneword-domains/main/expired.csv) | Expired | CSV with `domain` and `status` | Secondary public extract |

The two WhoisFreaks files are described by their public repository as daily free subsets. The UniqueDomains repository describes its file as a daily public extract of expired one-word domains, but only 1,000 rows rather than its larger live catalog. All three files are downloaded through ordinary public HTTPS GET requests with a descriptive User-Agent. No login, CAPTCHA, interactive scraping, anti-bot bypass, or paid API is used.

The collector preserves source provenance through `source`, `source_count`, and `sources` fields. If the same `.COM` domain appears in more than one source, it is deduplicated and the source list is retained.

## Explicitly excluded inventory

The collector and hunter reject any record whose lifecycle label contains pending, pending delete, redemption, auction, backorder, expiring, pre-release, prerelease, bidding, buy now, or aftermarket markers. The pipeline does not treat a pending-delete list as an expired/dropped hand-registration feed. ExpiredDomains.net and Instant Domain Search are not scraped because their reviewed public surfaces are interactive and do not expose a documented public raw API/feed suitable for unattended use.

## ExpiredDomains.net assessment

The official [ExpiredDomains.net FAQ](https://www.expireddomains.net/faq/) says that the site has no API. Its [availability explanation](https://www.expireddomains.net/article/how-the-domain-availability-check-works-15107.html) says that bulk DNS checks are not reliable proof of registration availability and that WHOIS/RDAP or registrar checks are more reliable. Accordingly, ExpiredDomains.net remains a documented manual research option, but it is not an automated runtime source unless the operator obtains an explicitly permitted public export/API endpoint in the future.

## Current availability verification

After lifecycle filtering, `.COM` filtering, deduplication, cheap quality/spam filtering, and initial scoring, the Hunter ranks all qualifying domains and selects the TOP 50. Verisign RDAP then enriches those TOP 50 candidates; RDAP is not a hard gate for appearing in the report. Wayback/history enrichment and final scoring are applied where appropriate:

- Official Verisign RDAP help: [Registration Data Access Protocol Help](https://www.verisign.com/news-insights/registration-data-access-protocol/help/)
- IANA registry mapping: [Bootstrap Service Registry for Domain Name Space](https://www.iana.org/assignments/rdap-dns/rdap-dns.xhtml)
- `.COM` base URL: `https://rdap.verisign.com/com/v1/`
- Domain lookup: `https://rdap.verisign.com/com/v1/domain/<domain>`

The implementation uses these outcomes:

| Outcome | Meaning | Can alert? |
|---|---|---:|
| `AVAILABLE` | Valid Verisign RDAP 404 error object with no domain object | Labeled AVAILABLE; verify at registrar |
| `REGISTERED` | Valid RDAP domain object | Labeled REGISTERED |
| `PENDING` | Domain object contains pending/hold lifecycle indicators | Labeled PENDING |
| `AUCTION` | Source lifecycle indicates auction, bidding, backorder, or aftermarket | Excluded before TOP 50 |
| `UNKNOWN` | Timeout, malformed response, rate limit, access restriction, server error, or other inconclusive response | Labeled UNKNOWN; may remain in TOP 50 |

An RDAP 404 is a point-in-time registry signal, not a guarantee that a registrar checkout will succeed. A name may be registered between the check and purchase, or subject to registry/registrar restrictions. The project therefore labels all results for manual verification and never treats `UNKNOWN` as available.

## Investor-style quality ranking and RDAP enrichment

The default scheduled RDAP budget remains 50. The hunter computes the revised investor-style quality score with neutral history for every locally acceptable candidate, ranking authentic short English words, selective natural two-word phrases, clear commercial meaning, brandability, and realistic end-user potential above generic or invented combinations. It penalizes awkward order, generic suffix stuffing, long three-or-more-word names, unrecognized tokens, spam patterns, and trademark-risk signals. The TOP 50 are selected by this quality score, not by source order or RDAP outcome. RDAP enriches up to 50 TOP 50 entries; a high-quality candidate with UNKNOWN status remains in the report and is never described as available.

Each successful feed records `ETag`, `Last-Modified`, parsed dataset date, content SHA-256, source URL, and feed name. These stable identifiers form the dataset fingerprint. Persistent state records each reported fingerprint, so the same dataset is not sent again. Scheduled polling occurs at 08:00, 14:00, and 20:00 UTC; because GitHub Actions is not an always-on feed listener, expected detection delay is near-zero to approximately six hours after a source update.

## Duplicate protection

`.state/processed_domains.json` is persisted through GitHub Actions cache. It stores dataset report fingerprints and timestamps alongside domain status records. A dataset marked sent is never reported again. If the source content hash or other reliable feed identity changes, the new dataset can produce a new TOP 50 report.

## Limitations

The WhoisFreaks free feeds are partial subsets and are not a complete registry-wide list of all expired or dropped `.COM` domains. The UniqueDomains feed is only a 1,000-row one-word extract. RDAP is status enrichment rather than a quality gate, and its point-in-time result is not a guarantee that registrar checkout will succeed. The pipeline favors transparent quality ranking and accurate availability labels over pretending to provide complete market coverage. It is intentionally acceptable for a poor dataset to contain no GOOD, STRONG, or EXCEPTIONAL domains.

## References

1. [WhoisFreaks daily expired and dropped domains](https://github.com/WhoisFreaks/daily-expired-and-dropped-domains)
2. [UniqueDomains expired one-word domains](https://github.com/UniqueDomains/expired-oneword-domains)
3. [Verisign RDAP documentation](https://www.verisign.com/news-insights/registration-data-access-protocol/help/)
4. [IANA RDAP DNS bootstrap registry](https://www.iana.org/assignments/rdap-dns/rdap-dns.xhtml)
5. [ExpiredDomains.net FAQ](https://www.expireddomains.net/faq/)
6. [ExpiredDomains.net availability-check explanation](https://www.expireddomains.net/article/how-the-domain-availability-check-works-15107.html)
7. [Instant Domain Search expired domains](https://instantdomainsearch.com/expired-domains)
