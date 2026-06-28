# app/__init__.py
# Application factory.

import os

from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager
from flask_wtf import CSRFProtect

login_manager = LoginManager()
csrf = CSRFProtect()


def _init_sentry():
    """Initialize Sentry error monitoring when SENTRY_DSN is set (no-op otherwise)."""
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            environment=os.getenv("FLASK_ENV", "production"),
        )
    except Exception:  # pragma: no cover - monitoring must never break boot
        pass


def create_app():
    load_dotenv()
    _init_sentry()

    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-this')
    app.config['ANTHROPIC_API_KEY'] = os.getenv('ANTHROPIC_API_KEY')
    app.config['LINKEDIN_CLIENT_ID'] = os.getenv('LINKEDIN_CLIENT_ID')
    app.config['LINKEDIN_CLIENT_SECRET'] = os.getenv('LINKEDIN_CLIENT_SECRET')
    app.config['LINKEDIN_REDIRECT_URI'] = os.getenv('LINKEDIN_REDIRECT_URI')

    from .services.llm_service import LLMService
    from .services.linkedin_api_service import LinkedInAPI

    app.extensions['llm_service'] = LLMService(api_key=app.config['ANTHROPIC_API_KEY'])
    app.extensions['linkedin_api'] = LinkedInAPI(
        client_id=app.config['LINKEDIN_CLIENT_ID'],
        client_secret=app.config['LINKEDIN_CLIENT_SECRET'],
        redirect_uri=app.config['LINKEDIN_REDIRECT_URI'],
    )

    # CSRF protection for all state-changing requests. JSON/fetch callers send
    # the token via the X-CSRFToken header (wired in base.html).
    csrf.init_app(app)

    # Authentication
    from .models.database import User, db_session

    login_manager.init_app(app)
    login_manager.login_view = 'routes.index'

    @login_manager.user_loader
    def load_user(user_id):
        user = db_session.get(User, user_id)
        # Soft-deleted accounts behave as logged out.
        if user is None or user.deleted_at is not None:
            return None
        return user

    @app.teardown_appcontext
    def remove_db_session(exc=None):
        db_session.remove()

    from .routes import routes
    app.register_blueprint(routes)

    return app
