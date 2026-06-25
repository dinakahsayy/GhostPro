# app/routes.py
# Web routes and JSON endpoints for the GhostPro prototype.

from datetime import datetime
from uuid import uuid4

from flask import (
    Blueprint, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

from .models.database import LinkedInPost, Session

routes = Blueprint('routes', __name__)


def _openai():
    return current_app.extensions['openai_service']


def _linkedin():
    return current_app.extensions['linkedin_api']


@routes.route('/')
def dashboard():
    return render_template('dashboard.html')


@routes.route('/generate')
def generate():
    return render_template('generate.html')


@routes.route('/history')
def history():
    with Session() as session_db:
        posts = session_db.query(LinkedInPost).order_by(LinkedInPost.timestamp.desc()).all()
        return render_template('history.html', posts=posts)


@routes.route('/templates')
def templates():
    return render_template('templates.html')


@routes.route('/linkedin/auth')
def linkedin_auth():
    state = uuid4().hex
    session['linkedin_oauth_state'] = state
    return redirect(_linkedin().get_authorization_url(state=state))


@routes.route('/linkedin/callback')
def linkedin_callback():
    try:
        expected_state = session.pop('linkedin_oauth_state', None)
        received_state = request.args.get('state')
        if not expected_state or expected_state != received_state:
            flash('LinkedIn OAuth state mismatch — please try again.', 'error')
            return redirect(url_for('routes.dashboard'))

        code = request.args.get('code')
        if not code:
            return "Authorization failed", 400

        token_data = _linkedin().get_access_token(code)
        if not token_data:
            return "Failed to get access token", 400

        # NOTE: storing the raw token in the Flask session is a placeholder.
        # Phase 1 will introduce per-user Fernet-encrypted token storage.
        session['linkedin_token'] = token_data.get('access_token')
        flash('Successfully connected to LinkedIn!', 'success')
        return redirect(url_for('routes.dashboard'))

    except Exception as e:
        current_app.logger.exception("LinkedIn callback error: %s", e)
        flash('Failed to connect to LinkedIn', 'error')
        return redirect(url_for('routes.dashboard'))


@routes.route('/generate-post', methods=['POST'])
def generate_post():
    try:
        data = request.get_json() or {}
        company_name = data.get('companyName', '')
        business_type = data.get('businessType', '')
        tone = data.get('tone', '')
        about_us = data.get('aboutUs', '')

        prompt_template = f"""
        Create a LinkedIn post for {company_name}, a {business_type} company.

        About the company:
        {about_us}

        The post should:
        - Use a {tone} tone of voice
        - Focus on business value and ROI
        - Include specific industry metrics or statistics
        - Address business decision-makers
        - Include relevant business hashtags
        - Keep emojis minimal and professional
        - End with a clear business-focused call to action

        Key themes to include:
        - Business efficiency
        - Industry expertise
        - Value proposition
        - Market leadership
        """

        generated_post = _openai().generate_post(prompt_template)
        if not generated_post:
            return jsonify({
                'status': 'error',
                'message': 'Failed to generate post - check server logs for details',
            }), 500

        with Session() as session_db:
            new_post = LinkedInPost(
                content=generated_post,
                timestamp=datetime.utcnow(),
                posted=False,
                company_name=company_name,
                business_type=business_type,
                tone=tone,
            )
            session_db.add(new_post)
            session_db.commit()
            return jsonify({
                'status': 'success',
                'post': generated_post,
                'post_id': new_post.id,
            })

    except Exception as e:
        current_app.logger.exception("generate_post error: %s", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@routes.route('/post-to-linkedin/<int:post_id>', methods=['POST'])
def post_to_linkedin(post_id):
    try:
        access_token = session.get('linkedin_token')
        if not access_token:
            return jsonify({
                'status': 'error',
                'message': 'Not authenticated with LinkedIn',
            }), 401

        with Session() as session_db:
            post = session_db.get(LinkedInPost, post_id)
            if not post:
                return jsonify({'status': 'error', 'message': 'Post not found'}), 404

            if not _linkedin().create_post(access_token, post.content):
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to post to LinkedIn',
                }), 500

            post.posted = True
            post.posted_at = datetime.utcnow()
            session_db.commit()
            return jsonify({'status': 'success'})

    except Exception as e:
        current_app.logger.exception("post_to_linkedin error: %s", e)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@routes.route('/delete-post/<int:post_id>', methods=['DELETE'])
def delete_post(post_id):
    try:
        with Session() as session_db:
            post = session_db.get(LinkedInPost, post_id)
            if not post:
                return jsonify({'status': 'error', 'message': 'Post not found'}), 404
            session_db.delete(post)
            session_db.commit()
            return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@routes.route('/edit-post/<int:post_id>', methods=['PUT'])
def edit_post(post_id):
    try:
        data = request.get_json() or {}
        with Session() as session_db:
            post = session_db.get(LinkedInPost, post_id)
            if not post:
                return jsonify({'status': 'error', 'message': 'Post not found'}), 404
            post.content = data.get('content', post.content)
            session_db.commit()
            return jsonify({'status': 'success', 'post': post.content})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@routes.route('/schedule-post/<int:post_id>', methods=['POST'])
def schedule_linkedin_post(post_id):
    try:
        data = request.get_json() or {}
        schedule_time = datetime.fromisoformat(data.get('schedule_time'))
        with Session() as session_db:
            post = session_db.get(LinkedInPost, post_id)
            if not post:
                return jsonify({'status': 'error', 'message': 'Post not found'}), 404
            post.scheduled_time = schedule_time
            session_db.commit()
            return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
