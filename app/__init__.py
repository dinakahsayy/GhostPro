# app/__init__.py
# Application factory.

import os

from dotenv import load_dotenv
from flask import Flask


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

    from .routes import routes
    app.register_blueprint(routes)

    return app
