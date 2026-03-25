import os
import secrets

SECRET_KEY = os.environ.get('SECRET_KEY', 'relay-challenge-2026-secret-key')

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, 'relay.db')
SQLALCHEMY_DATABASE_URI = f'sqlite:///{DATABASE_PATH}'
SQLALCHEMY_TRACK_MODIFICATIONS = False

ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'challenge2026!')

CHALLENGE_DATA_PATH = os.path.join(BASE_DIR, 'challenge_data.txt')

NUM_GROUPS = 10
PARTICIPANTS_PER_GROUP = 13

SESSION_COOKIE_NAME = 'relay_session'
SESSION_COOKIE_SAMESITE = 'Lax'
PERMANENT_SESSION_LIFETIME = 28800  # 8 hours in seconds
