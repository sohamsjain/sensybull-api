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

    @app.cli.command('sync-logos')
    @click.option('--all', 'sync_all', is_flag=True,
                  help='Sync every company with a ticker (default: watchlisted only).')
    @with_appcontext
    def sync_logos_cmd(sync_all):
        """Fetch company logo marks from the Benzinga Logo API."""
        from flask import current_app
        from app.services.logo_sync import sync_logos
        api_key = current_app.config.get('BENZINGA_API_KEY')
        if not api_key:
            click.echo('BENZINGA_API_KEY not set — skipping logo sync')
            return
        updated, considered = sync_logos(api_key, only_watchlisted=not sync_all)
        click.echo(f'Synced logos: {updated} updated of {considered} companies')
