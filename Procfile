web: gunicorn main:app --bind 0.0.0.0:$PORT --workers 2
worker: celery -A app.celery_app.celery worker --queues generation,publishing,source_watch,maintenance --loglevel info
beat: celery -A app.celery_app.celery beat --loglevel info
release: alembic upgrade head
