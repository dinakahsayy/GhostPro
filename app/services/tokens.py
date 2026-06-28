# app/services/tokens.py
# Returns a usable LinkedIn access token for a user, transparently refreshing it
# when it is missing or about to expire (§9.4). Returns None when no valid token
# can be obtained, so callers can mark the post errored and prompt a reconnect.

from datetime import datetime, timedelta
from ..utils.timeutil import utcnow

REFRESH_BUFFER = timedelta(minutes=5)


def ensure_valid_token(session, user, linkedin_api, now=None):
    now = now or utcnow()
    token = user.linkedin_access_token
    expires_at = user.token_expires_at

    needs_refresh = token is None or (expires_at is not None and now >= expires_at - REFRESH_BUFFER)
    if not needs_refresh:
        return token

    refresh_token = user.linkedin_refresh_token
    if refresh_token:
        data = linkedin_api.refresh_access_token(refresh_token)
        if data and data.get("access_token"):
            user.linkedin_access_token = data["access_token"]
            if data.get("refresh_token"):
                user.linkedin_refresh_token = data["refresh_token"]
            if data.get("expires_in"):
                user.token_expires_at = now + timedelta(seconds=int(data["expires_in"]))
            return user.linkedin_access_token

    # Couldn't refresh: a token with unknown expiry is still worth trying; an
    # expired one is not.
    return token if expires_at is None else None
