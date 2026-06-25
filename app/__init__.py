# app/__init__.py
# Application factory.

import os

from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager

login_manager = LoginManager()


def create_app():
    load_dotenv()

    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-this')
    app.config['OPENAI_API_KEY'] = os.getenv('OPENAI_API_KEY')
    app.config['LINKEDIN_CLIENT_ID'] = os.getenv('LINKEDIN_CLIENT_ID')
    app.config['LINKEDIN_CLIENT_SECRET'] = os.getenv('LINKEDIN_CLIENT_SECRET')
    app.config['LINKEDIN_REDIRECT_URI'] = os.getenv('LINKEDIN_REDIRECT_URI')

    from .services.openai_service import OpenAIService
    from .services.linkedin_api_service import LinkedInAPI

    app.extensions['openai_service'] = OpenAIService(api_key=app.config['OPENAI_API_KEY'])
    app.extensions['linkedin_api'] = LinkedInAPI(
        client_id=app.config['LINKEDIN_CLIENT_ID'],
        client_secret=app.config['LINKEDIN_CLIENT_SECRET'],
        redirect_uri=app.config['LINKEDIN_REDIRECT_URI'],
    )

    # Authentication
    from .models.database import User, db_session

    login_manager.init_app(app)
    login_manager.login_view = 'routes.index'

    @login_manager.user_loader
    def load_user(user_id):
        return db_session.get(User, user_id)

    @app.teardown_appcontext
    def remove_db_session(exc=None):
        db_session.remove()

    from .routes import routes
    app.register_blueprint(routes)

    return app
