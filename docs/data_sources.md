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

Before quality filtering, history checks, scoring, or alerting, every candidate is checked against Verisign’s authoritative `.COM` RDAP service:

- Official Verisign RDAP help: [Registration Data Access Protocol Help](https://www.verisign.com/news-insights/registration-data-access-protocol/help/)
- IANA registry mapping: [Bootstrap Service Registry for Domain Name Space](https://www.iana.org/assignments/rdap-dns/rdap-dns.xhtml)
- `.COM` base URL: `https://rdap.verisign.com/com/v1/`
- Domain lookup: `https://rdap.verisign.com/com/v1/domain/<domain>`

The implementation uses these outcomes:

| Outcome | Meaning | Can alert? |
|---|---|---:|
| `AVAILABLE` | Valid Verisign RDAP 404 error object with no domain object | Yes, subject to score and deduplication |
| `REGISTERED` | Valid RDAP domain object | No |
| `PENDING` | Domain object contains pending/hold lifecycle indicators | No |
| `AUCTION` | Source lifecycle indicates auction, bidding, backorder, or aftermarket | No |
| `UNKNOWN` | Timeout, malformed response, rate limit, access restriction, server error, or other inconclusive response | No |

An RDAP 404 is a point-in-time registry signal, not a guarantee that a registrar checkout will succeed. A name may be registered between the check and purchase, or subject to registry/registrar restrictions. The project therefore labels all results for manual verification and never treats `UNKNOWN` as available.

## Duplicate protection

`.state/processed_domains.json` is persisted through GitHub Actions cache. A domain marked sent is never alerted again. Recently processed registered, pending, auction, filtered, and other non-qualifying domains are cooled down. UNKNOWN results and unsent qualifying AVAILABLE results can be retried later so temporary RDAP or Telegram failures are not treated as permanent.

## Limitations

The WhoisFreaks free feeds are partial subsets and are not a complete registry-wide list of all expired or dropped `.COM` domains. The UniqueDomains feed is only a 1,000-row one-word extract. These sources do not supply a complete guarantee of current hand-registerability, which is why the independent Verisign RDAP check is mandatory. The pipeline favors correctness and compliance over pretending to provide complete market coverage.

## References

1. [WhoisFreaks daily expired and dropped domains](https://github.com/WhoisFreaks/daily-expired-and-dropped-domains)
2. [UniqueDomains expired one-word domains](https://github.com/UniqueDomains/expired-oneword-domains)
3. [Verisign RDAP documentation](https://www.verisign.com/news-insights/registration-data-access-protocol/help/)
4. [IANA RDAP DNS bootstrap registry](https://www.iana.org/assignments/rdap-dns/rdap-dns.xhtml)
5. [ExpiredDomains.net FAQ](https://www.expireddomains.net/faq/)
6. [ExpiredDomains.net availability-check explanation](https://www.expireddomains.net/article/how-the-domain-availability-check-works-15107.html)
7. [Instant Domain Search expired domains](https://instantdomainsearch.com/expired-domains)
