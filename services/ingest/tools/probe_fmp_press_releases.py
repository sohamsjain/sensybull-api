"""
probe_fmp_press_releases.py — check FMP's press-release feed before enabling it.

Run from services/ingest with FMP_API_KEY and SEC_USER_AGENT set:

    PYTHONPATH=. python tools/probe_fmp_press_releases.py            # summary
    PYTHONPATH=. python tools/probe_fmp_press_releases.py --gates    # + every row's verdict
    PYTHONPATH=. python tools/probe_fmp_press_releases.py --save out.json

It reports what the pipeline depends on and can't be checked offline:
  - row shape (field names) and the publisher/site mix → wire mapping;
  - publishedDate vs. now, so PR_FMP_TIMEZONE can be confirmed (the newest
    row should be minutes old, not ~4h old or in the future);
  - `text` length (a teaser — the article page is always fetched);
  - with --gates, the deterministic gate chain per release (language,
    thin-body check, law-firm blocklist, ticker, issuer). No LLM calls,
    nothing published.
"""

import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone

from press_release import fmp


def main(argv: list[str]) -> None:
    rows = fmp.fetch_latest()
    print(f"rows: {len(rows)}")
    if not rows:
        return
    if "--save" in argv:
        path = argv[argv.index("--save") + 1]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        print(f"saved raw rows to {path}")

    print(f"fields: {sorted(set().union(*(r.keys() for r in rows)))}")
    print("publisher × site × mapped wire:")
    mix = Counter((r.get("publisher"), r.get("site"), fmp.wire_for(r) or "-- DROPPED") for r in rows)
    for (pub, site, wire), n in mix.most_common():
        print(f"  {n:4d}  {pub!r:28} {site!r:28} → {wire}")

    newest = rows[0].get("publishedDate", "")
    iso = fmp._to_iso(newest)
    now = datetime.now(timezone.utc)
    print(f"newest publishedDate raw={newest!r} → {iso} (now {now.isoformat(timespec='seconds')})")
    if iso:
        age = (now - datetime.fromisoformat(iso)).total_seconds() / 60
        print(f"  → newest row is {age:.1f} min old under PR_FMP_TIMEZONE. "
              "Hours off or negative means the timezone is wrong.")
    oldest = fmp._to_iso(rows[-1].get("publishedDate", ""))
    print(f"window: {oldest} … {iso}")

    lens = [len(r.get("text") or "") for r in rows]
    print(f"text length: min={min(lens)} median={statistics.median(lens):.0f} max={max(lens)} "
          f"(FMP text is a teaser — every release costs an article fetch)")

    releases, other = fmp.parse_fmp_rows(rows)
    multi = [r for r in releases if len(r.metadata_tickers) > 1]
    print(f"releases after folding by URL: {len(releases)} (other-wire rows {other}, "
          f"multi-symbol {len(multi)})")

    if "--gates" in argv:
        _gates(releases)


def _gates(releases) -> None:
    from fetcher import load_ticker_map
    from press_release import issuer, materiality
    from press_release.feeds import fetch_release_body
    from press_release.language import is_english

    index = issuer.build_ticker_index(load_ticker_map())
    print(f"\nSEC ticker index: {len(index)}\n")
    verdicts = Counter()
    for r in releases:
        body = fetch_release_body(r)
        if not is_english(f"{r.headline}\n{body}"):
            verdict = "drop non_english"
        elif reason := materiality.prefilter_reason(r.headline, body):
            verdict = f"drop prefilter ({reason})"
        elif blocked := issuer.is_blocklisted(r.headline, body):
            verdict = f"drop blocklist ({blocked})"
        else:
            tickers = issuer.extract_tickers(r.headline, body, r.metadata_tickers)
            company, kind = issuer.resolve_issuer(index, tickers, r.issuer_name, r.headline, body)
            if not tickers:
                verdict = "drop no_ticker"
            elif company is None:
                verdict = f"drop issuer ({tickers})"
            else:
                verdict = f"→ LLM as {company['ticker']} [{kind}]"
        verdicts[verdict.split(" (")[0].split(" as ")[0]] += 1
        print(f"[{r.wire:13}] {','.join(r.metadata_tickers):10} {verdict:45} {r.headline[:90]}")
    print("\n" + "  ".join(f"{k}={v}" for k, v in verdicts.most_common()))


if __name__ == "__main__":
    main(sys.argv[1:])
