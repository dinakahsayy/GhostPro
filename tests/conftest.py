import os
import tempfile

# Configure the environment BEFORE importing anything from app, so the module-level
# engine binds to an isolated temp database and a deterministic encryption key.
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ["FLASK_ENV"] = "development"
_TMPDIR = tempfile.mkdtemp(prefix="ghostpro-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMPDIR}/test.db"

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.models.database import Base, Session, User, engine  # noqa: E402

Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def clean_db():
    """Wipe users (and cascading rows) before each test for isolation."""
    with Session() as s:
        for user in s.query(User).all():
            s.delete(user)
        s.commit()
    yield


@pytest.fixture
def app():
    app = create_app()
    # CSRF is exercised in test_csrf.py with a dedicated app; disable it for the
    # JSON-fetch route tests that don't carry a token.
    app.config["WTF_CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()
