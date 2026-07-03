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

    @app.cli.command('backfill-reactions')
    @click.option('--days', default=7, show_default=True,
                  help='Create reaction rows for events filed in the last N days.')
    @with_appcontext
    def backfill_reactions_cmd(days):
        """Create missing PriceReaction rows for recent events (all immediately due)."""
        from app.services.market_data.reaction_worker import backfill_reactions
        created, events = backfill_reactions(days=days)
        click.echo(f'Backfilled {created} reaction rows across {events} events')
