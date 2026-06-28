# app/services/llm_service.py
# Thin wrapper around the Anthropic (Claude) API. The client is built lazily so
# the app can boot even when ANTHROPIC_API_KEY is not yet configured.

import os

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 1024


class LLMService:
    def __init__(self, api_key=None):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            api_key = self._api_key or os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            self._client = Anthropic(api_key=api_key)
        return self._client

    def chat(self, system, user, model=DEFAULT_MODEL, temperature=0.7, max_tokens=DEFAULT_MAX_TOKENS):
        """Single-turn completion. Returns the text, or None on any failure
        (including a missing API key) so callers can degrade gracefully."""
        try:
            response = self._get_client().messages.create(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
            )
            parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
            return "".join(parts) if parts else None
        except Exception as e:
            print(f"LLM error: {e}")
            return None

    def generate_post(self, prompt_template):
        return self.chat(
            system="You are a professional LinkedIn content creator.",
            user=prompt_template,
        )
