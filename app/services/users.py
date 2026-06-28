# app/services/users.py
# User creation/onboarding persistence, kept out of the route layer so it can be
# unit-tested directly against a session.

from datetime import datetime, timedelta
from ..utils.timeutil import utcnow

from ..models.database import StyleProfile, User

# Fields copied straight from onboarding payload onto the User row.
_USER_TEXT_FIELDS = (
    "name", "title", "company", "industry", "headline", "bio",
    "audience_description", "age_range", "posting_mode", "post_frequency",
    "preferred_time", "timezone",
)
# Fields copied onto the user's StyleProfile.
_STYLE_TEXT_FIELDS = ("tone", "preferred_length", "content_goal")


def upsert_user_from_userinfo(session, userinfo, token_data):
    """Create or update a User from LinkedIn OpenID userinfo + token response.

    Matches an existing user by email first, then by LinkedIn member id (sub).
    Stores the (auto-encrypted) tokens and computes token expiry.
    """
    email = userinfo.get("email")
    sub = userinfo.get("sub")

    user = None
    if email:
        user = session.query(User).filter_by(email=email).first()
    if user is None and sub:
        user = session.query(User).filter_by(linkedin_id=sub).first()
    if user is None:
        # email is required+unique; fall back to a synthetic address if LinkedIn
        # didn't return one (email scope not granted).
        user = User(email=email or f"{sub}@linkedin.local")
        session.add(user)

    if sub:
        user.linkedin_id = sub
    if not user.name and userinfo.get("name"):
        user.name = userinfo.get("name")

    user.linkedin_access_token = token_data.get("access_token")
    if token_data.get("refresh_token"):
        user.linkedin_refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    if expires_in:
        user.token_expires_at = utcnow() + timedelta(seconds=int(expires_in))

    return user


def save_onboarding(session, user, data):
    """Persist onboarding answers onto the user and their StyleProfile, then mark
    onboarding complete. `data` is a plain dict (parsed from the form/JSON)."""
    for field in _USER_TEXT_FIELDS:
        value = data.get(field)
        if value not in (None, ""):
            setattr(user, field, value)

    if "preferred_days" in data and data["preferred_days"] is not None:
        user.preferred_days = data["preferred_days"]
    if "notification_email" in data:
        user.notification_email = bool(data["notification_email"])
    if "notification_inapp" in data:
        user.notification_inapp = bool(data["notification_inapp"])

    profile = user.style_profile
    if profile is None:
        profile = StyleProfile(user=user)
        session.add(profile)

    for field in _STYLE_TEXT_FIELDS:
        value = data.get(field)
        if value not in (None, ""):
            setattr(profile, field, value)
    if data.get("emoji_usage") not in (None, ""):
        profile.emoji_usage = int(data["emoji_usage"])
    if data.get("hashtag_count") not in (None, ""):
        profile.hashtag_count = float(data["hashtag_count"])
    if "top_topics" in data and data["top_topics"] is not None:
        profile.top_topics = data["top_topics"]
    if "avoid_topics" in data and data["avoid_topics"] is not None:
        profile.avoid_topics = data["avoid_topics"]

    user.onboarding_complete = True
    return user
