"""
Shared pytest fixtures for the Sensybull API test suite.

Uses an in-memory SQLite database so tests are fast and isolated.
Each test function gets a fresh database via the `db_session` fixture.
"""

import os
import pytest

# Set test env vars BEFORE any app imports
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["SOCKETIO_ASYNC_MODE"] = "threading"

from unittest.mock import patch

from app import create_app, db as _db
from app.models.user import User
from app.models.company import Company
from app.models.watchlist import Watchlist
from app.models.filing_event import FilingEvent
from app.models.event_type import EventType
from app.models.catalyst import Catalyst
from config import Config


class TestConfig(Config):
    """Override config for testing."""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    RATELIMIT_ENABLED = False


@pytest.fixture(scope="session")
def app():
    """Create the Flask app once per test session."""
    with patch("app.services.company_loader.ensure_companies_loaded"), \
         patch("app.services.realtime.subscriber.start_subscriber"):
        application = create_app(config_class=TestConfig)
    yield application


@pytest.fixture(autouse=True)
def db_session(app):
    """
    Create all tables before each test and drop them after.
    Each test gets a clean database.
    """
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def sample_user(db_session):
    """Create and return a sample user with password 'testpass123'."""
    user = User(name="Test User", email="test@example.com")
    user.set_password("testpass123")
    db_session.session.add(user)
    db_session.session.commit()
    return user


@pytest.fixture
def sample_company(db_session):
    """Create and return a sample company."""
    company = Company(name="Apple Inc.", ticker="AAPL", cik="0000320193")
    db_session.session.add(company)
    db_session.session.commit()
    return company


@pytest.fixture
def sample_company_2(db_session):
    """Create and return a second sample company."""
    company = Company(name="Tesla Inc.", ticker="TSLA", cik="0001318605")
    db_session.session.add(company)
    db_session.session.commit()
    return company


@pytest.fixture
def sample_watchlist(db_session, sample_user, sample_company):
    """Create a watchlist with one company."""
    wl = Watchlist(name="Tech", user_id=sample_user.id)
    wl.companies.append(sample_company)
    db_session.session.add(wl)
    db_session.session.commit()
    return wl


@pytest.fixture
def auth_headers(client, sample_user):
    """Login and return Authorization headers."""
    resp = client.post("/api/v1/auth/login", json={
        "email": "test@example.com",
        "password": "testpass123",
    })
    token = resp.get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_event(db_session, sample_company):
    """Create a sample filing event."""
    event = FilingEvent(
        edgar_id="test-edgar-001",
        signal_type="8-K",
        company_id=sample_company.id,
        cik=sample_company.cik,
        ticker=sample_company.ticker,
        company_name=sample_company.name,
        max_tier=1,
        items_json=[{"number": "1.01", "title": "Material Agreement", "tier": 2, "category": "Contracts", "text": "Test"}],
        exhibits_json=[],
        briefing_json={"headline": "Test headline", "summary": "Test summary"},
        event_types_json=["Acquisition"],
    )
    event.event_types.append(EventType(type_name="Acquisition"))
    event.catalysts.append(Catalyst(
        event_description="Shareholder vote",
        catalyst_date=None,
        ticker="AAPL",
        company_name="Apple Inc.",
    ))
    db_session.session.add(event)
    db_session.session.commit()
    return event
