# Automatic expired-domain sources and hand-registration verification

## Source used by the scheduled main pipeline

The scheduled collector uses exactly one automatic input source:

| Feed | Source lifecycle definition | Format | Main-pipeline role |
|---|---|---|---|
| [WhoisFreaks latest free expired list](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv) | Domains that have expired or entered an expiry-related lifecycle stage | One domain per line | **Only automatic source** |

The feed is retrieved with an ordinary public HTTPS GET request and a descriptive User-Agent. It does not require a login, CAPTCHA, interactive scraping, anti-bot bypass, or paid API. The source is a partial daily subset, not a complete registry-wide list.

The source provider’s own README uses **expired** broadly: it includes domains that have expired or entered an expiry-related stage such as redemption or pending delete. Therefore, the source label is not treated as proof of hand-registration availability. Current lifecycle and availability are verified separately through authoritative Verisign `.COM` RDAP before any domain can be considered a final candidate.

The default feed set contains only `whoisfreaks_expired`. The WhoisFreaks dropped feed and UniqueDomains extract are not loaded by the main pipeline. The checked-in CSV remains an explicit local fallback only; the scheduled workflow does not pass it, so a failed automatic source causes the scheduled collection step to fail instead of silently reusing stale or unverified data.

## Sources investigated but excluded

### ExpiredDomains.net

The official [ExpiredDomains.net FAQ](https://www.expireddomains.net/faq/) states that the site has no API. It also warns that using a program, script, bot, crawler, or AI agent to connect to the member area, and any data mining in that area, can lead to account closure. The official [expired-domain page](https://www.expireddomains.net/expired-domains/) is explicitly titled **Pending Delete Domains**, while the home page distinguishes pending-delete expired domains from deleted/dropped domains that are available after release.

ExpiredDomains.net is therefore a manual research reference only. It is not scraped, automated, or used as a runtime source.

### Expired-Domains.com

[Expired-Domains.com](https://www.expired-domains.com/) combines expiring, dropped, pre-release, pending-delete, auctions, marketplace, and deleted inventory. Its [Terms of Service](https://www.expired-domains.com/terms) explicitly prohibit automated retrieval, scraping, crawling, bots, bulk scripted requests, and use of APIs or endpoints outside normal browser operation. It is excluded.

### DNSExit

[DNSExit’s expired-domain page](https://dnsexit.com/domains/search/expired-pending-delete-domains) explicitly combines recently expired and pending-delete names, includes pending-delete reservations, and also lists names that will expire within days. It is not an expired-only hand-registration source and was not selected.

### UniqueDomains

The [UniqueDomains public GitHub extract](https://github.com/UniqueDomains/expired-oneword-domains) claims `status=expired` and is refreshed daily, but it is only a 1,000-row multi-TLD extract. Its public documentation does not establish that the status excludes redemption, pending delete, marketplace, or auction inventory. On 2026-08-19 it contained only 7 `.COM` rows, and authoritative RDAP sampling found both a redemption-period domain and registered domains. It is excluded from the main pipeline.

### WhoisFreaks dropped feed

The [WhoisFreaks dropped feed](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv) is a separate lifecycle category. It is not merged with expired data, not relabeled as expired, and cannot enter the main TOP 50.

## Source validation evidence from 2026-08-19

The following public files were downloaded directly for the source investigation:

| Source artifact | Raw rows/lines | Valid unique `.COM` rows | Rejected or non-`.COM` |
|---|---:|---:|---:|
| WhoisFreaks expired feed | 10,000 | 5,002 | 4,998 |
| WhoisFreaks dropped control feed | 10,000 | 3,881 | 6,119 |
| UniqueDomains public extract | 1,000 | 7 | 993 |

The WhoisFreaks expired-feed sample included authoritative RDAP results with `pending delete`, `redemption period`, and registered/hold states. The UniqueDomains sample included both `redemption period` and registered states. This confirms that a source row labeled `expired` is not automatically hand-registerable and must not bypass lifecycle validation.

The source audit files and hashes are retained outside the repository for the execution record. The selected feed’s 2026-08-19 content SHA-256 was `41a643165d29eb6fd0fcc6a410579affac817be63878d8015c903b3103770fb7`.

## Lifecycle and availability verification

The collector admits only the selected expired feed and only syntactically valid `.COM` domains. Any non-expired lifecycle feed passed through explicit test or local arguments is marked `excluded` and contributes zero records. Pending delete, redemption, auction, backorder, expiring, pre-release, bidding, buy-now, and aftermarket records are never relabeled as expired.

After source filtering, `.COM` filtering, deduplication, cheap quality/spam filtering, and strict initial 0–10 scoring, the Hunter sends the strongest initial candidates to Verisign RDAP. Only `AVAILABLE` results continue to Wayback/history and final scoring. The final output is capped at 50 but may contain fewer than 50 or zero entries. `AVAILABLE` plus final score `>= 7.0/10` is mandatory for a final candidate.

| Outcome | Meaning | Hand-registration treatment |
|---|---|---|
| `AVAILABLE` | Valid Verisign `.COM` RDAP 404 error object with no domain object | Candidate for normal registration; verify at the registrar immediately before purchase |
| `REGISTERED` | Valid RDAP domain object without pending/hold markers | Not hand-registerable |
| `PENDING` | RDAP domain object contains pending-delete, redemption, server-hold, or client-hold indicators | Excluded from final candidates |
| `AUCTION` | Source lifecycle indicates auction, bidding, backorder, or aftermarket | Excluded from final candidates |
| `UNKNOWN` | Timeout, malformed response, rate limit, access restriction, server error, or other inconclusive response | Never treated as available |

The `.COM` endpoint is `https://rdap.verisign.com/com/v1/domain/<domain>`. The implementation follows the [Verisign RDAP documentation](https://www.verisign.com/news-insights/registration-data-access-protocol/help/) and the [IANA RDAP bootstrap registry](https://www.iana.org/assignments/rdap-dns/rdap-dns.xhtml). A valid RDAP 404 is a point-in-time registry signal, not a guarantee that registrar checkout will succeed.

## Ranking, schedules, and duplicate protection

The investor-quality scorer uses a strict 0–10 model with natural-language quality, brandability, commercial/end-user demand, shortness, keyword quality, resale potential, and broad clean market appeal. The default scheduled RDAP budget remains 50 and is spent on the strongest initial candidates, not arbitrary source order. Only AVAILABLE candidates receive Wayback/history checks and final scoring. The final report preserves source provenance, scores, classifications, explanations, and truthful run counts.

Each successful feed records `ETag`, `Last-Modified`, parsed dataset date, content SHA-256, source URL, and feed name. These stable identifiers form the dataset fingerprint. Persistent state records each reported fingerprint, so the same dataset is not sent again. Scheduled polling remains at **08:00, 14:00, and 20:00 UTC**, with manual `workflow_dispatch` preserved.

`.state/processed_domains.json` is persisted through GitHub Actions cache. The scheduled workflow does not use the manual CSV fallback, which prevents an old or unverified file from being mistaken for a current expired dataset.

## Limitations

The selected WhoisFreaks feed is the strongest compliant free machine-readable source found, but it is only a 10,000-row daily subset and its own definition includes expiry-related stages such as redemption and pending delete. Consequently, the system cannot claim that every raw row is immediately hand-registerable. Verisign RDAP is the authoritative point-in-time gate: `REGISTERED`, `PENDING`, `AUCTION`, and `UNKNOWN` are rejected from the final list, and no final candidate is produced unless it is `AVAILABLE` and scores at least 7.0/10. If zero domains qualify, the system sends the required zero-result message rather than filling the list.

## References

1. [WhoisFreaks daily expired and dropped domains](https://github.com/WhoisFreaks/daily-expired-and-dropped-domains)
2. [WhoisFreaks public expired feed](https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv)
3. [UniqueDomains expired one-word domains](https://github.com/UniqueDomains/expired-oneword-domains)
4. [Expired-Domains.com](https://www.expired-domains.com/)
5. [Expired-Domains.com Terms of Service](https://www.expired-domains.com/terms)
6. [DNSExit expired and pending-delete search](https://dnsexit.com/domains/search/expired-pending-delete-domains)
7. [ExpiredDomains.net FAQ](https://www.expireddomains.net/faq/)
8. [ExpiredDomains.net pending-delete list](https://www.expireddomains.net/expired-domains/)
9. [Verisign RDAP documentation](https://www.verisign.com/news-insights/registration-data-access-protocol/help/)
10. [IANA RDAP bootstrap registry](https://www.iana.org/assignments/rdap-dns/rdap-dns.xhtml)
