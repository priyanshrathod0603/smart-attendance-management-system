"""End-to-end tests for the server-side authentication boundary."""
import re
from datetime import timedelta

import pytest

from app import create_app
from auth import _hash
from database.auth_models import AuthSession, User, utcnow
from database.db import db


PASSWORD = "CorrectHorse1"


@pytest.fixture
def app_client():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "AUTH_SESSION_HOURS": 8,
    })
    with app.app_context():
        db.drop_all()
        db.create_all()
    with app.test_client() as client:
        yield app, client
    with app.app_context():
        db.session.remove()
        db.drop_all()


def form_csrf(response):
    token = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert token, "the HTML form must include a CSRF token"
    return token.group(1)


def page_csrf(response):
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    assert token, "authenticated pages must publish the CSRF token for API calls"
    return token.group(1)


def signup(client, email="admin@example.test"):
    response = client.get("/signup")
    response = client.post("/signup", data={
        "csrf_token": form_csrf(response),
        "name": "Test Administrator",
        "email": email,
        "password": PASSWORD,
    })
    assert response.status_code == 302
    return page_csrf(client.get("/"))


def test_unauthenticated_requests_are_contained(app_client):
    _, client = app_client
    dashboard = client.get("/")
    assert dashboard.status_code == 302
    assert "/login" in dashboard.headers["Location"]
    assert client.get("/api/students").status_code == 401
    assert client.post("/api/students", json={}).status_code == 401


def test_signup_login_logout_and_csrf(app_client):
    _, client = app_client
    csrf = signup(client)

    # State-changing routes reject an omitted or forged CSRF token.
    assert client.post("/logout").status_code == 400
    assert client.post("/logout", data={"csrf_token": "forged"}).status_code == 400

    logged_out = client.post("/logout", data={"csrf_token": csrf})
    assert logged_out.status_code == 302
    assert "/login" in logged_out.headers["Location"]
    assert client.get("/").status_code == 302

    login = client.get("/login")
    failed = client.post("/login", data={
        "csrf_token": form_csrf(login), "email": "admin@example.test", "password": "incorrect",
    })
    assert failed.status_code == 401

    login = client.get("/login")
    successful = client.post("/login", data={
        "csrf_token": form_csrf(login), "email": "admin@example.test", "password": PASSWORD, "remember": "on",
    })
    assert successful.status_code == 302
    assert successful.headers["Location"].endswith("/")


def test_expired_or_revoked_server_session_cannot_be_reused(app_client):
    app, client = app_client
    signup(client)
    with client.session_transaction() as browser_session:
        raw_sid = browser_session["sid"]
    with app.app_context():
        record = AuthSession.query.filter_by(token_hash=_hash(raw_sid)).one()
        record.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()

    assert client.get("/").status_code == 302
    with client.session_transaction() as browser_session:
        assert "sid" not in browser_session


def test_employee_cannot_write_privileged_api_resources(app_client):
    app, client = app_client
    signup(client)
    with app.app_context():
        user = User.query.filter_by(email="admin@example.test").one()
        user.role = "employee"
        db.session.commit()

    dashboard = client.get("/")
    csrf = page_csrf(dashboard)
    denied = client.post("/api/teachers", json={"teacher_id": "T-1", "name": "No Access"}, headers={"X-CSRF-Token": csrf})
    assert denied.status_code == 403


def test_unconfigured_oauth_providers_fail_gracefully(app_client):
    _, client = app_client
    for provider in ("google", "apple", "microsoft"):
        response = client.get(f"/oauth/{provider}")
        assert response.status_code == 503
        assert f"{provider.title()} sign-in is not configured" in response.get_data(as_text=True)
