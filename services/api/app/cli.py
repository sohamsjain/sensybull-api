"""Flask CLI commands."""

import click
from flask.cli import with_appcontext


def register_cli(app):
    """Register custom CLI commands with the Flask app."""

    @app.cli.command('sync-companies')
    @with_appcontext
    def sync_companies_cmd():
        """FMP listed common stocks → the company table (add, rename, de-list)."""
        from app.services.company_loader import sync_companies
        stats = sync_companies()
        click.echo(
            f"Synced companies: {stats['universe']} listed common stocks; "
            f"{stats['added']} added, {stats['renamed']} renamed, "
            f"{stats['share_classes']} share classes aliased, {stats['delisted']} de-listed, "
            f"{stats['aliases']} feed tickers aliased, {stats['pending']} left for the next run"
            + ('' if stats['complete'] else ' (universe incomplete: de-listing skipped)'))

    @app.cli.command('check-company-universe')
    @with_appcontext
    def check_company_universe_cmd():
        """Live-check FMP's screener against what sync-companies assumes."""
        from app.services.company_loader import run_checks
        results = run_checks()
        for name, status, detail in results:
            click.echo(f'[{status.upper():4}] {name}: {detail}')
        if any(status == 'fail' for _, status, _ in results):
            raise SystemExit(1)

    @app.cli.command('sync-market-data')
    @with_appcontext
    def sync_market_data_cmd():
        """Refresh shares outstanding, last price and market cap (all FMP)."""
        from app.models.company import Company
        from app.services.market_data.sync import sync_market_data
        shares, prices = sync_market_data()
        caps = Company.query.filter(Company.market_cap.isnot(None)).count()
        click.echo(
            f'Market data synced: shares updated for {shares}, prices for {prices} '
            f'companies; {caps} companies have market caps'
        )

    @app.cli.command('check-market-data')
    @with_appcontext
    def check_market_data_cmd():
        """Live-check FMP quotes/bars against the assumptions prices.py makes."""
        from app.models.company import Company
        from app.services.market_data.check import FAIL, run_checks
        from app.services.market_data.prices import QUOTE_BATCH
        tickers = [c.ticker for c in (Company.query
                                      .filter(Company.ticker.isnot(None))
                                      .filter(Company.market_cap.isnot(None))
                                      .order_by(Company.market_cap.desc())
                                      .limit(QUOTE_BATCH))]
        results = run_checks(tickers)
        for name, status, detail in results:
            click.echo(f'[{status.upper():4}] {name}: {detail}')
        if any(status == FAIL for _, status, _ in results):
            raise SystemExit(1)

    @app.cli.command('sync-fundamentals')
    @click.option('--symbols', default='', help='Comma-separated tickers to sync (default: the cron queue).')
    @click.option('--limit', default=None, type=int, help='Max companies to fetch this run.')
    @click.option('--full', is_flag=True, help='Refresh every synced company, not just stale ones.')
    @with_appcontext
    def sync_fundamentals_cmd(symbols, limit, full):
        """FMP → fundamentals tables (backfill new companies, refresh recent filers)."""
        from app.services.fundamentals.sync import run_sync
        symbol_list = [s.strip() for s in symbols.split(',') if s.strip()] or None
        result = run_sync(symbols=symbol_list, limit=limit, full=full)
        click.echo(f'Fundamentals sync: {result}')

    @app.cli.command('rebuild-fundamentals')
    @with_appcontext
    def rebuild_fundamentals_cmd():
        """Recompute derived ratios from stored periods + today's prices (no FMP calls)."""
        from app.services.fundamentals.sync import rebuild_all_snapshots
        n = rebuild_all_snapshots()
        click.echo(f'Rebuilt {n} fundamentals snapshots')

    @app.cli.command('backfill-reactions')
    @click.option('--days', default=7, show_default=True,
                  help='Create reaction rows for events filed in the last N days.')
    @with_appcontext
    def backfill_reactions_cmd(days):
        """Create missing PriceReaction rows for recent events (all immediately due)."""
        from app.services.market_data.reaction_worker import backfill_reactions
        created, events = backfill_reactions(days=days)
        click.echo(f'Backfilled {created} reaction rows across {events} events')

    @app.cli.command('reset-reactions')
    @click.option('--days', default=30, show_default=True,
                  help='Re-measure intraday reactions for events filed in the last N days.')
    @with_appcontext
    def reset_reactions_cmd(days):
        """Re-queue 5m-1h / at-open reactions so the worker re-measures them."""
        from app.services.market_data.reaction_worker import reset_intraday_reactions
        n = reset_intraday_reactions(days=days)
        click.echo(f'Reset {n} intraday reaction rows; the worker re-measures them within a few minutes')
