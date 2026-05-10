web: gunicorn wsgi:app --workers 2 --worker-class sync --threads 2 --timeout 120 --bind 0.0.0.0:$PORT --max-requests 500 --max-requests-jitter 50 --preload
