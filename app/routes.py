# app/routes.py
# Web routes and JSON endpoints for GhostPro.

import os
from datetime import datetime
from uuid import uuid4

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)
from flask_login import (
    current_user, login_required, login_user, logout_user,
)

from .models.database import LinkedInPost, Post, Session, User, db_session
from .services.generation import generate_post_for_user, post_to_dict
from .services.inbox import (
    create_inbox_item, get_inbox_item, inbox_item_to_dict, list_inbox_items,
    skip_inbox_item, soft_delete_inbox_item, toggle_priority, update_inbox_item,
)
from .services.scheduler import ensure_schedule
from .services.style_profile import generate_style_profile
from .services.users import save_onboarding, upsert_user_from_userinfo

routes = Blueprint('routes', __name__)


def _openai():
    return current_app.extensions['openai_service']


def _linkedin():
    return current_app.extensions['linkedin_api']


def _is_dev():
    return os.getenv('FLASK_ENV') == 'development'


# ---------------------------------------------------------------------------
# Public landing / auth
# ---------------------------------------------------------------------------
@routes.route('/')
def index():
    """Public landing. Authenticated users are sent on to onboarding/dashboard."""
    if current_user.is_authenticated:
        if not current_user.onboarding_complete:
            return redirect(url_for('routes.onboarding'))
        return redirect(url_for('routes.dashboard'))
    return render_template('index.html', dev_login=_is_dev())


@routes.route('/linkedin/auth')
def linkedin_auth():
    state = uuid4().hex
    session['linkedin_oauth_state'] = state
    return redirect(_linkedin().get_authorization_url(state=state))


@routes.route('/linkedin/callback')
def linkedin_callback():
    try:
        expected_state = session.pop('linkedin_oauth_state', None)
        if not expected_state or expected_state != request.args.get('state'):
            flash('LinkedIn sign-in state mismatch — please try again.', 'error')
            return redirect(url_for('routes.index'))

        code = request.args.get('code')
        if not code:
            flash('LinkedIn sign-in was cancelled.', 'error')
            return redirect(url_for('routes.index'))

        token_data = _linkedin().get_access_token(code)
        if not token_data or not token_data.get('access_token'):
            flash('Could not complete LinkedIn sign-in.', 'error')
            return redirect(url_for('routes.index'))

        userinfo = _linkedin().get_userinfo(token_data['access_token'])
        if not userinfo:
            flash('Could not read your LinkedIn profile.', 'error')
            return redirect(url_for('routes.index'))

        user = upsert_user_from_userinfo(db_session, userinfo, token_data)
        db_session.commit()
        login_user(user)

        flash('Connected to LinkedIn.', 'success')
        if not user.onboarding_complete:
            return redirect(url_for('routes.onboarding'))
        return redirect(url_for('routes.dashboard'))

    except Exception as e:
        db_session.rollback()
        current_app.logger.exception("LinkedIn callback error: %s", e)
        flash('Failed to connect to LinkedIn.', 'error')
        return redirect(url_for('routes.index'))


@routes.route('/dev/login')
def dev_login():
    """Development-only shortcut to sign in as a test user without LinkedIn."""
    if not _is_dev():
        abort(404)
    user = db_session.query(User).filter_by(email='dev@ghostpro.local').first()
    if user is None:
        user = User(email='dev@ghostpro.local', name='Dev User')
        db_session.add(user)
        db_session.commit()
    login_user(user)
    if not user.onboarding_complete:
        return redirect(url_for('routes.onboarding'))
    return redirect(url_for('routes.dashboard'))


@routes.route('/auth/logout', methods=['POST', 'GET'])
def logout():
    logout_user()
    flash('Signed out.', 'success')
    return redirect(url_for('routes.index'))


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------
@routes.route('/onboarding')
@login_required
def onboarding():
    if current_user.onboarding_complete:
        return redirect(url_for('routes.dashboard'))
    return render_template('onboarding.html')


@routes.route('/onboarding/prefill')
@login_required
def onboarding_prefill():
    """Return known user fields so the wizard can pre-fill and ask for confirmation."""
    u = current_user
    return jsonify({
        'name': u.name,
        'title': u.title,
        'company': u.company,
        'industry': u.industry,
        'headline': u.headline,
        'bio': u.bio,
        'timezone': u.timezone,
    })


@routes.route('/onboarding/save', methods=['POST'])
@login_required
def onboarding_save():
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        user = db_session.get(User, current_user.get_id())
        save_onboarding(db_session, user, data)
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        current_app.logger.exception("onboarding_save error: %s", e)
        return jsonify({'status': 'error', 'message': str(e)}), 400

    # Best-effort: build an initial style summary from the onboarding answers.
    # Never blocks onboarding completion (e.g. when no OpenAI key is configured).
    try:
        generate_style_profile(db_session, user, _openai())
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        current_app.logger.warning("style profile generation failed: %s", e)

    # Create the user's recurring post schedule (§9.1).
    try:
        ensure_schedule(db_session, user)
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        current_app.logger.warning("schedule creation failed: %s", e)

    return jsonify({'status': 'success', 'redirect': url_for('routes.dashboard')})


# ---------------------------------------------------------------------------
# Authenticated app
# ---------------------------------------------------------------------------
@routes.route('/dashboard')
@login_required
def dashboard():
    if not current_user.onboarding_complete:
        return redirect(url_for('routes.onboarding'))
    return render_template('dashboard.html', user=current_user)


# ---------------------------------------------------------------------------
# Content Inbox (§7.3)
# ---------------------------------------------------------------------------
@routes.route('/inbox', methods=['GET'])
@login_required
def inbox():
    """List view. Returns JSON when ?format=json, otherwise the page."""
    if request.args.get('format') == 'json':
        items = list_inbox_items(
            db_session, current_user,
            status=request.args.get('status'),
            priority=request.args.get('priority'),
        )
        return jsonify([inbox_item_to_dict(i) for i in items])
    return render_template('inbox.html')


@routes.route('/inbox', methods=['POST'])
@login_required
def inbox_create():
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        item = create_inbox_item(
            db_session, current_user,
            content_type=data.get('content_type'),
            raw_content=data.get('raw_content'),
            priority=data.get('priority', 'use_whenever'),
            context_note=data.get('context_note'),
        )
        db_session.commit()
    except ValueError as e:
        db_session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except Exception as e:
        db_session.rollback()
        current_app.logger.exception("inbox_create error: %s", e)
        return jsonify({'status': 'error', 'message': 'Could not save item'}), 500
    return jsonify({'status': 'success', 'item': inbox_item_to_dict(item)}), 201


@routes.route('/inbox/<int:item_id>', methods=['GET'])
@login_required
def inbox_get(item_id):
    item = get_inbox_item(db_session, current_user, item_id)
    if not item:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    return jsonify(inbox_item_to_dict(item))


@routes.route('/inbox/<int:item_id>', methods=['PUT'])
@login_required
def inbox_update(item_id):
    item = get_inbox_item(db_session, current_user, item_id)
    if not item:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    try:
        update_inbox_item(db_session, item, request.get_json(silent=True) or {})
        db_session.commit()
    except ValueError as e:
        db_session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 400
    return jsonify({'status': 'success', 'item': inbox_item_to_dict(item)})


@routes.route('/inbox/<int:item_id>/prioritize', methods=['POST'])
@login_required
def inbox_prioritize(item_id):
    item = get_inbox_item(db_session, current_user, item_id)
    if not item:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    toggle_priority(item)
    db_session.commit()
    return jsonify({'status': 'success', 'item': inbox_item_to_dict(item)})


@routes.route('/inbox/<int:item_id>/skip', methods=['POST'])
@login_required
def inbox_skip(item_id):
    item = get_inbox_item(db_session, current_user, item_id)
    if not item:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    skip_inbox_item(item)
    db_session.commit()
    return jsonify({'status': 'success', 'item': inbox_item_to_dict(item)})


@routes.route('/inbox/<int:item_id>', methods=['DELETE'])
@login_required
def inbox_delete(item_id):
    item = get_inbox_item(db_session, current_user, item_id)
    if not item:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    soft_delete_inbox_item(item)
    db_session.commit()
    return jsonify({'status': 'success'})


@routes.route('/generate')
@login_required
def generate():
    return render_template('generate.html')


@routes.route('/history')
@login_required
def history():
    with Session() as session_db:
        posts = session_db.query(LinkedInPost).order_by(LinkedInPost.timestamp.desc()).all()
        return render_template('history.html', posts=posts)


@routes.route('/templates')
@login_required
def templates():
    return render_template('templates.html')


# ---------------------------------------------------------------------------
# Posts — new generation pipeline (§7.4, §8)
# ---------------------------------------------------------------------------
@routes.route('/posts/generate', methods=['POST'])
@login_required
def posts_generate():
    """Generate a draft post from the user's highest-priority source (inbox first)."""
    try:
        user = db_session.get(User, current_user.get_id())
        post = generate_post_for_user(db_session, user, _openai())
        if post is None:
            db_session.rollback()
            return jsonify({
                'status': 'error',
                'message': 'Could not generate a post — check that an OpenAI API key is configured.',
            }), 502
        db_session.commit()
        return jsonify({'status': 'success', 'post': post_to_dict(post)}), 201
    except Exception as e:
        db_session.rollback()
        current_app.logger.exception("posts_generate error: %s", e)
        return jsonify({'status': 'error', 'message': 'Could not generate a post'}), 500


@routes.route('/posts', methods=['GET'])
@login_required
def posts_list():
    posts = (
        db_session.query(Post)
        .filter(Post.user_id == current_user.get_id())
        .order_by(Post.created_at.desc())
        .all()
    )
    return jsonify([post_to_dict(p) for p in posts])


@routes.route('/posts/<int:post_id>', methods=['GET'])
@login_required
def posts_get(post_id):
    post = db_session.get(Post, post_id)
    if post is None or post.user_id != current_user.get_id():
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    return jsonify(post_to_dict(post))


@routes.route('/post-to-linkedin/<int:post_id>', methods=['POST'])
@login_required
def post_to_linkedin(post_id):
    try:
        access_token = current_user.linkedin_access_token
        if not access_token:
            return jsonify({
                'status': 'error',
                'message': 'No LinkedIn connection on file — please reconnect.',
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
@login_required
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
@login_required
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
@login_required
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
