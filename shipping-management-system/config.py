import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    
    # Database configuration
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_recycle': 280,
        'pool_pre_ping': True,
    }
    
    # Do not force a SERVER_NAME by default so the app can run locally.
    SERVER_NAME = None
    PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'http')
    BASE_URL = os.environ.get('BASE_URL') or ''
    # Toggle domain redirect behavior (set to '1' or 'true' to enable)
    ENABLE_DOMAIN_REDIRECT = os.environ.get('ENABLE_DOMAIN_REDIRECT', '0') in ('1', 'true', 'True')
    # Public hostname (used for generating links and client-side rewrite)
    PUBLIC_HOSTNAME = os.environ.get('PUBLIC_HOSTNAME') or None
    
    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY') or ''
    
    # Mail configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS') or True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    
    # Application settings
    PACKAGE_CODE_LENGTH = 20
    DEFAULT_TIMEZONE = 'UTC'
    # Session / cookie security
    # Determine whether cookies should be marked Secure. For local/dev
    # environments accessed over plain HTTP we should not mark cookies
    # Secure (otherwise browsers will refuse to set them). Allow an
    # explicit env var to override in deployments.
    _env_val = os.environ.get('SESSION_COOKIE_SECURE')
    if _env_val is None:
        SESSION_COOKIE_SECURE = (os.environ.get('PREFERRED_URL_SCHEME', 'http') == 'https')
    else:
        SESSION_COOKIE_SECURE = _env_val in ('1', 'true', 'True')
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
    _remember_env = os.environ.get('REMEMBER_COOKIE_SECURE')
    if _remember_env is None:
        REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    else:
        REMEMBER_COOKIE_SECURE = _remember_env in ('1', 'true', 'True')
    REMEMBER_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get('PERMANENT_SESSION_HOURS', '1')))
    
    # Rate calculation constants
    BASE_RATES = {
        'air': {'express': 15.0, 'standard': 10.0, 'freight': 8.0},
        'ground': {'express': 8.0, 'standard': 5.0, 'freight': 3.0},
        'sea': {'express': 5.0, 'standard': 3.0, 'freight': 1.5}
    }
    
    # Weight multipliers
    WEIGHT_RATES = {
        'documents': 1.0,
        'electronics': 2.5,
        'fragile': 2.0,
        'general': 1.5
    }