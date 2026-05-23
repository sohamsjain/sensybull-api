import sqlalchemy as sa
from app import db

# Many-to-many association table between Watchlist and Company
watchlist_companies = sa.Table(
    'watchlist_companies',
    db.metadata,
    sa.Column('watchlist_id', sa.String(36), sa.ForeignKey('watchlist.id'), primary_key=True),
    sa.Column('company_id', sa.String(36), sa.ForeignKey('company.id'), primary_key=True),
)
