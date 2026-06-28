import re

import pytest

from app import create_app
from app.models.database import Session, User


@pytest.fixture
def csrf_client():
    """A client with CSRF protection ENABLED (the production default)."""
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = True
    with Session() as s:  # clean dev user for a deterministic login
        for u in s.query(User).filter_by(email="dev@ghostpro.local").all():
            s.delete(u)
        s.commit()
    return app.test_client()


def _csrf_token(client):
    """Pull the token from a rendered page's meta tag."""
    html = client.get("/", follow_redirects=True).get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return match.group(1)


def test_mutating_request_without_token_is_rejected(csrf_client):
    csrf_client.get("/dev/login")  # GET login is allowed
    resp = csrf_client.post("/inbox", json={"content_type": "text_note", "raw_content": "x"})
    assert resp.status_code == 400  # Flask-WTF rejects missing CSRF token


def test_mutating_request_with_header_token_succeeds(csrf_client):
    csrf_client.get("/dev/login")
    token = _csrf_token(csrf_client)
    resp = csrf_client.post(
        "/inbox",
        json={"content_type": "text_note", "raw_content": "with token"},
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 201


def test_get_requests_need_no_token(csrf_client):
    csrf_client.get("/dev/login")
    assert csrf_client.get("/inbox?format=json").status_code == 200


def test_logout_is_post_only(csrf_client):
    csrf_client.get("/dev/login")
    # GET is no longer allowed for logout.
    assert csrf_client.get("/auth/logout").status_code == 405
