# app/services/linkedin_api_service.py
# LinkedIn OAuth 2.0 + UGC Posts API wrapper.

import logging
import os
from urllib.parse import urlencode

import requests


class LinkedInAPI:
    def __init__(self, client_id=None, client_secret=None, redirect_uri=None):
        self.client_id = client_id or os.getenv('LINKEDIN_CLIENT_ID')
        self.client_secret = client_secret or os.getenv('LINKEDIN_CLIENT_SECRET')
        self.redirect_uri = redirect_uri or os.getenv('LINKEDIN_REDIRECT_URI')
        self.base_url = "https://api.linkedin.com/v2"
        self.logger = logging.getLogger(__name__)

    def get_authorization_url(self, state):
        """Build the LinkedIn authorization URL. Caller must supply a CSRF state."""
        if not state:
            raise ValueError("state is required")
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': 'openid profile w_member_social email',
            'state': state,
        }
        return f"https://www.linkedin.com/oauth/v2/authorization?{urlencode(params)}"

    def get_access_token(self, auth_code):
        try:
            response = requests.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    'grant_type': 'authorization_code',
                    'code': auth_code,
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'redirect_uri': self.redirect_uri,
                },
                timeout=10,
            )
            if response.status_code != 200:
                self.logger.error(f"Token exchange failed: {response.status_code}")
                return None
            return response.json()
        except Exception as e:
            self.logger.error(f"Error exchanging auth code: {e}")
            return None

    def refresh_access_token(self, refresh_token):
        """Exchange a refresh token for a new access token. Returns the token
        response dict or None on failure."""
        if not refresh_token:
            return None
        try:
            response = requests.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                },
                timeout=10,
            )
            if response.status_code != 200:
                self.logger.error(f"Token refresh failed: {response.status_code}")
                return None
            return response.json()
        except Exception as e:
            self.logger.error(f"Error refreshing token: {e}")
            return None

    def get_userinfo(self, access_token):
        """Fetch the OpenID Connect userinfo for the authenticated member.

        Returns the parsed dict (sub, name, given_name, family_name, email,
        picture, locale) or None on failure.
        """
        try:
            response = requests.get(
                'https://api.linkedin.com/v2/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10,
            )
            if response.status_code != 200:
                self.logger.error(f"Userinfo error: {response.status_code}")
                return None
            return response.json()
        except Exception as e:
            self.logger.error(f"Error fetching userinfo: {e}")
            return None

    def get_engagement(self, access_token, post_urn):
        """Fetch like/comment counts for a published post via socialActions.
        Returns {likes, comments, shares} or None on failure."""
        if not post_urn:
            return None
        try:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'X-Restli-Protocol-Version': '2.0.0',
            }
            resp = requests.get(
                f"{self.base_url}/socialActions/{post_urn}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code != 200:
                self.logger.error(f"Engagement fetch failed: {resp.status_code}")
                return None
            data = resp.json()
            return {
                'likes': (data.get('likesSummary') or {}).get('totalLikes', 0),
                'comments': (data.get('commentsSummary') or {}).get('totalComments', 0),
                'shares': 0,  # not exposed by socialActions; synced as 0 for now
            }
        except Exception as e:
            self.logger.error(f"Error fetching engagement: {e}")
            return None

    def create_post(self, access_token, content):
        try:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'X-Restli-Protocol-Version': '2.0.0',
            }

            user_data = self.get_userinfo(access_token)
            if not user_data:
                return False

            person_id = user_data.get('sub')

            post_response = requests.post(
                f"{self.base_url}/ugcPosts",
                headers=headers,
                json={
                    "author": f"urn:li:person:{person_id}",
                    "lifecycleState": "PUBLISHED",
                    "specificContent": {
                        "com.linkedin.ugc.ShareContent": {
                            "shareCommentary": {"text": content},
                            "shareMediaCategory": "NONE",
                        }
                    },
                    "visibility": {
                        "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                    },
                },
                timeout=10,
            )

            if post_response.status_code != 201:
                self.logger.error(f"Post creation failed: {post_response.status_code}")
                return None
            # Return the created post's URN so callers can store linkedin_post_id.
            return post_response.headers.get("x-restli-id") or "posted"

        except Exception as e:
            self.logger.error(f"Error creating LinkedIn post: {e}")
            return False
