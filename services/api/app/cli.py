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

