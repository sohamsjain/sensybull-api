# services/api/app/services/market_data/check.py
"""
Live checks of the FMP assumptions `prices.py` is built on.

The unit tests pin how FMP's shapes are converted; they can't tell whether
FMP actually behaves the way the conversion assumes. Run
`flask check-market-data` with the production key after changing the plan
or the price code. Each check returns (name, status, detail), where
status is "ok", "warn" (works, with a caveat worth knowing) or "fail" (a
conversion assumption is wrong and prices will be off).
"""

from datetime import datetime, time as dtime, timedelta, timezone

from app.services.market_data import prices

OK, WARN, FAIL = "ok", "warn", "fail"


def _parse(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def _eastern(t: str) -> datetime:
    return _parse(t).astimezone(prices.EASTERN)


def _recent_sessions(symbol: str, count: int) -> list[dict]:
    """The last `count` complete daily bars (today's may still be forming)."""
    now = datetime.now(timezone.utc)
    daily = prices.get_bars(symbol, prices.DAILY, now - timedelta(days=count * 2 + 10), now)
    today = now.astimezone(prices.EASTERN).date()
    closed = [b for b in daily if _eastern(b["t"]).date() < today]
    return closed[-count:]


def check_quotes() -> tuple[str, str, str]:
    symbols = ["AAPL", "MSFT", "BRK-B"]
    quotes = prices.get_quotes(symbols)
    missing = [s for s in symbols if not prices.quote_price(quotes.get(s))]
    if missing:
        return "quotes", FAIL, f"no price for {missing} (got keys {sorted(quotes)})"
    no_prev = [s for s in symbols if not prices.quote_prev_close(quotes[s])]
    no_time = [s for s in symbols if not prices.quote_time(quotes[s])]
    if no_prev or no_time:
        return "quotes", FAIL, f"previousClose missing for {no_prev}, timestamp missing for {no_time}"
    q = quotes["AAPL"]
    return "quotes", OK, (f"AAPL {prices.quote_price(q)} prev {prices.quote_prev_close(q)} "
                          f"as of {prices.quote_time(q)}; BRK-B resolved in dash form")


def check_quote_batch(tickers: list[str]) -> tuple[str, str, str]:
    """A full batch of real tickers comes back mostly whole (no silent cap)."""
    batch = [prices.normalize_ticker(t) for t in tickers][:prices.QUOTE_BATCH]
    if len(batch) < 20:
        return "quote batch", WARN, f"only {len(batch)} tickers to test with"
    quotes = prices.get_quotes(batch)
    share = len(quotes) / len(batch)
    status = OK if share >= 0.8 else FAIL
    return "quote batch", status, (f"{len(quotes)}/{len(batch)} symbols returned in one call"
                                   + ("" if status == OK else " — FMP may cap batch size; lower QUOTE_BATCH"))


def check_split_adjustment() -> tuple[str, str, str]:
    """NVDA split 10:1 effective 2024-06-10; adjusted closes stay continuous."""
    bars = prices.get_bars("NVDA", prices.DAILY,
                           datetime(2024, 6, 3, tzinfo=timezone.utc),
                           datetime(2024, 6, 15, tzinfo=timezone.utc))
    if len(bars) < 8:
        return "split adjustment", FAIL, f"only {len(bars)} NVDA bars for 2024-06-03..14"
    closes = [b["c"] for b in bars]
    ratio = max(closes) / min(closes)
    if ratio > 1.5:
        return "split adjustment", FAIL, f"NVDA closes jump {ratio:.1f}x across the split — unadjusted"
    return "split adjustment", OK, f"NVDA closes {min(closes):.2f}–{max(closes):.2f} across the split"


def check_intraday_timezone(session: dict) -> tuple[str, str, str]:
    """The 09:30 ET 1-min bar lands at 09:30 ET and opens at the day's open."""
    day = _eastern(session["t"]).date()
    open_at = datetime.combine(day, dtime(9, 30), tzinfo=prices.EASTERN)
    bars = prices.get_bars("AAPL", "1min", open_at - timedelta(minutes=1),
                           open_at + timedelta(minutes=5))
    first = next((b for b in bars if _parse(b["t"]) >= open_at), None)
    if first is None:
        return "intraday timezone", FAIL, f"no AAPL 1-min bar at/after 09:30 ET on {day}"
    offset = _parse(first["t"]) - open_at
    drift = abs(first["o"] - session["o"]) / session["o"]
    if offset > timedelta(minutes=2) or drift > 0.005:
        return "intraday timezone", FAIL, (
            f"first bar after 09:30 ET on {day} is at {first['t']} opening {first['o']} "
            f"vs daily open {session['o']} — FMP timestamps may not be US/Eastern")
    return "intraday timezone", OK, f"{day}: 09:30 ET bar at {first['t']}, open {first['o']} ≈ daily {session['o']}"


def check_extended_hours(session: dict) -> tuple[str, str, str]:
    day = _eastern(session["t"]).date()
    start = datetime.combine(day, dtime(4, 0), tzinfo=prices.EASTERN)
    bars = prices.get_bars("AAPL", "1min", start, start + timedelta(hours=16))
    pre = sum(1 for b in bars if _eastern(b["t"]).time() < dtime(9, 30))
    post = sum(1 for b in bars if _eastern(b["t"]).time() >= dtime(16, 0))
    if post == 0:
        return "extended hours", WARN, (
            f"{day}: no AAPL 1-min bars after 16:00 ET ({pre} pre-market) — after-hours "
            "filings get their +5m..+1h reactions at the next open")
    return "extended hours", OK, f"{day}: {pre} pre-market and {post} after-hours 1-min bars"


def check_chunk_sizes() -> list[tuple[str, str, str]]:
    """One full-size intraday request returns every bar it should.

    Each window is exactly one chunk (INTRADAY_CHUNK_DAYS) ending yesterday,
    so a row cap on FMP's side shows up as missing bars.
    """
    out = []
    per_session = {"1min": 390, "15min": 26, "1hour": 7}
    last_day = datetime.now(prices.EASTERN).date() - timedelta(days=1)
    for interval, bars_per_session in per_session.items():
        span = prices.INTRADAY_CHUNK_DAYS[interval]
        first_day = last_day - timedelta(days=span - 1)
        start = datetime.combine(first_day, dtime(0, 0), tzinfo=prices.EASTERN)
        end = datetime.combine(last_day, dtime(23, 59), tzinfo=prices.EASTERN)
        sessions = prices.get_bars("AAPL", prices.DAILY, start, end)
        bars = prices.get_bars("AAPL", interval, start, end)
        # Every bar counts: extended-hours bars can only pad the total, and
        # hourly bars may be stamped on the hour rather than at :30. Half
        # days (early closes) trade fewer bars; 90% absorbs them.
        expected = len(sessions) * bars_per_session
        name = f"{interval} × {span}d chunk"
        if not expected:
            out.append((name, WARN, "no sessions in the window"))
            continue
        status = OK if len(bars) / expected >= 0.9 else FAIL
        detail = f"{len(bars)} bars for {len(sessions)} sessions (≥{expected} expected)"
        if status == FAIL:
            detail += f" — FMP truncates; shrink INTRADAY_CHUNK_DAYS['{interval}']"
        out.append((name, status, detail))
    return out


def run_checks(sample_tickers: list[str]) -> list[tuple[str, str, str]]:
    results = [check_quotes(), check_quote_batch(sample_tickers), check_split_adjustment()]
    sessions = _recent_sessions("AAPL", 5)
    if not sessions:
        results.append(("recent sessions", FAIL, "no AAPL daily bars for the last two weeks"))
        return results
    results.append(check_intraday_timezone(sessions[-1]))
    results.append(check_extended_hours(sessions[-1]))
    results.extend(check_chunk_sizes())
    return results

