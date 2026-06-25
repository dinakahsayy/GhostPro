# app/services/openai_service.py
# Thin wrapper around the OpenAI client. The client is built lazily so that
# the app can boot even when OPENAI_API_KEY is not yet configured.

import os

from openai import OpenAI


class OpenAIService:
    def __init__(self, api_key=None):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            api_key = self._api_key or os.getenv('OPENAI_API_KEY')
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not configured")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def generate_post(self, prompt_template):
        try:
            response = self._get_client().chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a professional LinkedIn content creator."},
                    {"role": "user", "content": prompt_template},
                ],
                temperature=0.7,
                max_tokens=500,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"OpenAI error: {e}")
            return None
