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

    def chat(self, system, user, model="gpt-4o", temperature=0.7, max_tokens=500):
        """Single-turn chat completion. Returns the text, or None on any failure
        (including a missing API key) so callers can degrade gracefully."""
        try:
            response = self._get_client().chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"OpenAI error: {e}")
            return None

    def generate_post(self, prompt_template):
        return self.chat(
            system="You are a professional LinkedIn content creator.",
            user=prompt_template,
            model="gpt-4",
            temperature=0.7,
            max_tokens=500,
        )
