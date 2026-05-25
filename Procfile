bot: python manage.py migrate --noinput && python manage.py run_bot
web: python manage.py collectstatic --noinput && gunicorn spanishtutor.wsgi --bind 0.0.0.0:$PORT
