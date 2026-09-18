"""Flask CLI commands."""

import click
from flask.cli import with_appcontext


def register_cli(app):
    """Register custom CLI commands with the Flask app."""

    @app.cli.command('sync-companies')
    @with_appcontext
    def sync_companies_cmd():
        """Fetch SEC company_tickers.json and upsert into the database."""
        from app.services.company_loader import sync_companies
        added, total = sync_companies()
        click.echo(f'Synced companies: {added} new, {total} total in SEC dataset')

    @app.cli.command('sync-market-data')
    @with_appcontext
    def sync_market_data_cmd():
        """Refresh shares outstanding (EDGAR) and last price/market cap (Alpaca)."""
        from app.models.company import Company
        from app.services.market_data.sync import sync_market_data
        shares, prices = sync_market_data()
        caps = Company.query.filter(Company.market_cap.isnot(None)).count()
        missing = (Company.query
                   .filter(Company.last_price.isnot(None))
                   .filter(Company.shares_outstanding.is_(None))
                   .count())
        click.echo(
            f'Market data synced: shares updated for {shares}, prices for {prices} '
            f'companies; {caps} companies have market caps, {missing} priced '
            f'companies still missing share counts (backfills on next runs)'
        )

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
