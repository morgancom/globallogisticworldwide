import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_file, make_response
from urllib.parse import unquote_plus
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_compress import Compress
import time
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func, and_, or_
from flask_babel import Babel, gettext
import json
import pandas as pd
import csv
import io
import re
from threading import Thread
import time
import random
import string
import math
import requests
from config import Config
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from dateutil import parser as date_parser
import qrcode
from PIL import Image
import base64
import uuid
import socket
from werkzeug.middleware.proxy_fix import ProxyFix

# Install pymysql as MySQLdb
#pymysql.install_as_MySQLdb()

app = Flask(__name__)
app.config.from_object(Config)

# Respect X-Forwarded headers when behind a reverse proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# Domain redirect logic disabled: running locally without forced public host.
# If you need to re-enable this behavior, implement a guarded redirect that
# respects `app.config['ENABLE_DOMAIN_REDIRECT']` and your runtime environment.
# Serve static files with a long cache by default (1 year)
app.config.setdefault('SEND_FILE_MAX_AGE_DEFAULT', 31536000)

# Initialize response compression
Compress(app)

# Initialize Babel for i18n
babel = Babel(app)
app.config.setdefault('BABEL_DEFAULT_LOCALE', 'en')
app.config.setdefault('BABEL_SUPPORTED_LOCALES', list(ALLOWED_LANGUAGES.keys()) if 'ALLOWED_LANGUAGES' in globals() else ['en'])

# Initialize extensions
db = SQLAlchemy(app)
# Avoid auto Eventlet monkey-patching: it is deprecated and can break with modern
# Flask/Werkzeug/Python combinations. Only enable Eventlet when explicitly asked
# for, and otherwise fall back to the safe threading model.
_async_mode = 'threading'
if os.environ.get('SOCKETIO_ASYNC_MODE', '').lower() == 'eventlet':
    try:
        import eventlet  # type: ignore
        try:
            eventlet.monkey_patch()
            _async_mode = 'eventlet'
        except Exception as exc:
            app.logger.warning('Eventlet requested but monkey-patching failed; falling back to threading: %s', exc)
            _async_mode = 'threading'
    except Exception as exc:
        app.logger.warning('Eventlet requested but not available; falling back to threading: %s', exc)
        _async_mode = 'threading'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_async_mode)
mail = Mail(app)
login_manager = LoginManager()
login_manager.init_app(app)
# Use the public-friendly login endpoint
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'


# Add strict security headers to responses to harden the app against common attacks.
@app.after_request
def set_security_headers(response):
    try:
        # Prevent MIME-sniffing
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        # Prevent clickjacking
        response.headers.setdefault('X-Frame-Options', 'DENY')
        # Referrer policy
        response.headers.setdefault('Referrer-Policy', 'no-referrer-when-downgrade')
        # Basic permissions policy (restrict powerful browser features)
        response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=()')

        # Content Security Policy: conservative default allowing self and trusted https sources
        # Allow fonts and styles from trusted CDNs so icon fonts (Font Awesome)
        # and other assets loaded over HTTPS can render correctly.
        csp = (
            "default-src 'self'; "
            "script-src 'self' https: 'unsafe-inline'; "
            "style-src 'self' https: 'unsafe-inline'; "
            "font-src 'self' https: data:; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https: wss:;"
        )
        response.headers.setdefault('Content-Security-Policy', csp)

        # HSTS only when configured to use HTTPS in production
        if app.config.get('PREFERRED_URL_SCHEME') == 'https' or os.environ.get('ENABLE_HSTS', '0') in ('1', 'true', 'True'):
            response.headers.setdefault('Strict-Transport-Security', 'max-age=63072000; includeSubDomains; preload')
    except Exception:
        # Never raise from header-setting — silently continue
        pass
    return response


@app.route('/login', methods=['GET', 'POST'])
def login():
    # Alias endpoint so templates and redirects can use `url_for('login')`.
    # Delegate to the existing admin_login handler to keep auth logic centralized.
    return admin_login()

# Customize unauthorized response so AJAX/JSON clients get JSON 401 instead of HTML redirect
@login_manager.unauthorized_handler
def handle_unauthorized():
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                 request.is_json or \
                 request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']

    if wants_json:
        return jsonify({'error': 'authentication_required'}), 401

    # Default behavior for normal browser navigation
    flash('Please log in to access this page.', 'warning')
    return redirect(url_for('login', next=request.url))
# ---------------- Language support ----------------
# Map of supported language codes to display names.
ALLOWED_LANGUAGES = {
    'en': 'English', 'es': 'Español', 'fr': 'Français', 'de': 'Deutsch',
    'it': 'Italiano', 'pt': 'Português', 'ru': 'Русский', 'zh': '中文',
    'ja': '日本語', 'ar': 'العربية', 'hi': 'हिन्दी', 'bn': 'বাংলা'
}


@app.context_processor
def inject_language():
    """Provide the current language name and code to all templates."""
    # Prefer session, fall back to cookie, then default
    lang_code = session.get('lang') or session.get('language') or request.cookies.get('lang') or 'en'
    return {
        'current_language': ALLOWED_LANGUAGES.get(lang_code, 'English'),
        'current_language_code': lang_code,
        'AVAILABLE_LANGUAGES': ALLOWED_LANGUAGES
    }


# ==================== Database Models ====================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum('admin', 'super_admin', 'viewer'), default='admin')
    full_name = db.Column(db.String(100))
    department = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    last_ip = db.Column(db.String(45))
    
    def set_password(self, password):
        # Use a strong PBKDF2 algorithm with SHA-512 and a high iteration count.
        # If you change the algorithm/iterations, ensure existing users are migrated
        # or supported (Werkzeug stores the method and iterations in the hash).
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha512:600000')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def has_permission(self, permission):
        # Super admin has all permissions
        if self.role == 'super_admin':
            return True
        # Define permissions by role
        permissions = {
            'admin': ['create', 'edit', 'view', 'update_location', 'hold', 'export'],
            'viewer': ['view']
        }
        return permission in permissions.get(self.role, [])

    @property
    def is_admin(self):
        """Return True for admin-like roles.

        This is resilient to legacy/variant role values (e.g. 'System Administrator').
        """
        try:
            return 'admin' in (self.role or '').lower()
        except Exception:
            return False


# Per-tab session tokens to support logout-per-tab and logout-everywhere
class SessionToken(db.Model):
    __tablename__ = 'session_tokens'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('session_tokens', lazy='dynamic'))

class Package(db.Model):
    __tablename__ = 'packages'
    id = db.Column(db.Integer, primary_key=True)
    tracking_code = db.Column(db.String(20), unique=True, nullable=False)
    
    # Sender information
    sender_name = db.Column(db.String(100), nullable=False)
    sender_company = db.Column(db.String(100))
    sender_address = db.Column(db.Text, nullable=False)
    sender_address2 = db.Column(db.String(100))
    sender_city = db.Column(db.String(50))
    sender_state = db.Column(db.String(50))
    sender_zip = db.Column(db.String(20))
    sender_country = db.Column(db.String(50), default='USA')
    sender_phone = db.Column(db.String(20), nullable=False)
    sender_email = db.Column(db.String(100), nullable=False)
    
    # Recipient information
    recipient_name = db.Column(db.String(100), nullable=False)
    recipient_company = db.Column(db.String(100))
    recipient_address = db.Column(db.Text, nullable=False)
    recipient_address2 = db.Column(db.String(100))
    recipient_city = db.Column(db.String(50))
    recipient_state = db.Column(db.String(50))
    recipient_zip = db.Column(db.String(20))
    recipient_country = db.Column(db.String(50), default='USA')
    recipient_phone = db.Column(db.String(20), nullable=False)
    recipient_email = db.Column(db.String(100), nullable=False)
    
    # Package details
    weight = db.Column(db.Float, nullable=False)
    length = db.Column(db.Float)
    width = db.Column(db.Float)
    height = db.Column(db.Float)
    dimensions_unit = db.Column(db.String(10), default='cm')
    declared_value = db.Column(db.Float)
    currency = db.Column(db.String(3), default='USD')
    package_type = db.Column(db.Enum('documents', 'electronics', 'fragile', 'perishable', 'dangerous', 'general'), default='general')
    shipping_type = db.Column(db.Enum('express', 'standard', 'freight', 'overnight'), default='standard')
    shipping_method = db.Column(db.Enum('air', 'ground', 'sea', 'rail'), default='ground')
    insurance_required = db.Column(db.Boolean, default=False)
    signature_required = db.Column(db.Boolean, default=False)
    
    # Route information
    pickup_address = db.Column(db.Text, nullable=False)
    pickup_lat = db.Column(db.Float)
    pickup_lng = db.Column(db.Float)
    destination_address = db.Column(db.Text, nullable=False)
    destination_lat = db.Column(db.Float)
    destination_lng = db.Column(db.Float)
    estimated_delivery = db.Column(db.DateTime)
    actual_delivery = db.Column(db.DateTime)
    
    # Tracking information
    current_location_lat = db.Column(db.Float)
    current_location_lng = db.Column(db.Float)
    current_location_name = db.Column(db.String(200))
    current_status = db.Column(db.String(50))
    # Extended status list to include shipped/dispatched and out_for_delivery
    status = db.Column(db.Enum('pending', 'shipped', 'dispatched', 'in_transit', 'out_for_delivery', 'on_hold', 'delivered', 'cancelled', 'returned'), default='pending')
    # Simulation/persistence fields for background simulation
    simulation_active = db.Column(db.Boolean, default=False, nullable=False)
    simulation_speed_kmh = db.Column(db.Float, default=60.0)
    simulation_interval = db.Column(db.Integer, default=5)
    simulation_started_at = db.Column(db.DateTime)
    
    # Metadata
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    notes = db.Column(db.Text)
    
    # Relationships
    milestones = db.relationship('RouteMilestone', backref='package', lazy='dynamic', cascade='all, delete-orphan')
    location_history = db.relationship('LocationHistory', backref='package', lazy='dynamic', cascade='all, delete-orphan', order_by='desc(LocationHistory.created_at)')
    hold_records = db.relationship('HoldRecord', backref='package', lazy='dynamic', cascade='all, delete-orphan', order_by='desc(HoldRecord.hold_start)')
    notifications = db.relationship('Notification', backref='package', lazy='dynamic', cascade='all, delete-orphan')

class RouteMilestone(db.Model):
    __tablename__ = 'route_milestones'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=False)
    sequence_order = db.Column(db.Integer, nullable=False)
    location_name = db.Column(db.String(200), nullable=False)
    location_type = db.Column(db.String(50), default='waypoint')
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    estimated_hours_from_start = db.Column(db.Integer)
    estimated_arrival = db.Column(db.DateTime)
    actual_arrival = db.Column(db.DateTime)
    is_completed = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class LocationHistory(db.Model):
    __tablename__ = 'location_history'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    location_name = db.Column(db.String(200))
    address = db.Column(db.Text)
    status = db.Column(db.String(50))
    event_type = db.Column(db.String(50), default='location_update')
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class HoldRecord(db.Model):
    __tablename__ = 'hold_records'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    reason_category = db.Column(db.Enum('customs', 'weather', 'address', 'recipient', 'payment', 'documentation', 'operational', 'other'), default='other')
    description = db.Column(db.Text)
    hold_start = db.Column(db.DateTime, default=datetime.utcnow)
    hold_end = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    resolved_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    resolved_at = db.Column(db.DateTime)
    # Optional coordinates where the package was held (entered by admin)
    hold_lat = db.Column(db.Float)
    hold_lng = db.Column(db.Float)
    hold_location_name = db.Column(db.String(200))

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), nullable=False)
    notification_type = db.Column(db.Enum('email', 'sms', 'push'), default='email')
    event_type = db.Column(db.Enum('tracking_created', 'status_update', 'location_update', 'on_hold', 'hold_released', 'delay', 'delivered'), nullable=False)
    recipient = db.Column(db.String(255))
    subject = db.Column(db.String(255))
    message = db.Column(db.Text)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.Enum('pending', 'sent', 'failed'), default='pending')
    error_message = db.Column(db.Text)

class DeliveryConfirmation(db.Model):
    __tablename__ = 'delivery_confirmations'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'), unique=True, nullable=False)
    received_by = db.Column(db.String(100))
    relationship = db.Column(db.String(50))
    signature_data = db.Column(db.Text)  # Base64 encoded signature
    signature_path = db.Column(db.String(255))
    delivery_photo_path = db.Column(db.String(255))
    delivery_notes = db.Column(db.Text)
    confirmed_at = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

class Driver(db.Model):
    __tablename__ = 'drivers'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    license_number = db.Column(db.String(50))
    license_expiry = db.Column(db.Date)
    vehicle_type = db.Column(db.String(50))
    vehicle_plate = db.Column(db.String(20))
    is_available = db.Column(db.Boolean, default=True)
    current_location_lat = db.Column(db.Float)
    current_location_lng = db.Column(db.Float)
    assigned_packages = db.Column(db.Integer, default=0)

class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.Integer, db.ForeignKey('packages.id'))
    expense_type = db.Column(db.Enum('fuel', 'toll', 'maintenance', 'labor', 'other'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), default='USD')
    description = db.Column(db.Text)
    incurred_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

# ==================== Utility Functions ====================

def generate_tracking_code(length=20):
    """Generate a 20-character alphanumeric tracking code: SE + YY + random."""
    prefix = 'SE'
    year_part = datetime.now().strftime('%y')                   # 2 chars
    random_len = max(0, length - len(prefix) - len(year_part))  # 20 - 2 - 2 = 16
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=random_len))
    return prefix + year_part + random_part

def calculate_delivery_date(shipping_method, shipping_type, start_date=None):
    """Calculate estimated delivery date based on shipping method and type"""
    if start_date is None:
        start_date = datetime.now()
    
    days_map = {
        'air': {'overnight': 1, 'express': 2, 'standard': 3, 'freight': 5},
        'ground': {'overnight': 1, 'express': 3, 'standard': 5, 'freight': 10},
        'sea': {'express': 7, 'standard': 14, 'freight': 21},
        'rail': {'express': 4, 'standard': 7, 'freight': 12}
    }
    
    days = days_map.get(shipping_method, {}).get(shipping_type, 5)
    return start_date + timedelta(days=days)

def calculate_shipping_cost(weight, package_type, shipping_method, shipping_type, declared_value=0):
    """Calculate shipping cost based on various factors"""
    base_rates = {
        'air': {'overnight': 25.0, 'express': 18.0, 'standard': 12.0, 'freight': 9.0},
        'ground': {'overnight': 20.0, 'express': 14.0, 'standard': 8.0, 'freight': 5.0},
        'sea': {'express': 10.0, 'standard': 6.0, 'freight': 3.0},
        'rail': {'express': 12.0, 'standard': 7.0, 'freight': 4.0}
    }
    
    type_multipliers = {
        'documents': 0.8,
        'electronics': 2.2,
        'fragile': 2.5,
        'perishable': 2.0,
        'dangerous': 3.0,
        'general': 1.2
    }
    
    base_rate = base_rates.get(shipping_method, {}).get(shipping_type, 10.0)
    type_multiplier = type_multipliers.get(package_type, 1.2)
    
    # Weight calculation (first kg base, then additional)
    if weight <= 1:
        weight_charge = base_rate
    else:
        weight_charge = base_rate + (weight - 1) * 2.5
    
    total = weight_charge * type_multiplier
    
    # Insurance if declared value > 100
    if declared_value > 100:
        insurance = declared_value * 0.01  # 1% insurance
        total += insurance
    
    return round(total, 2)

def send_email_notification(app, mail, recipient, subject, template):
    """Send email notification"""
    try:
        msg = Message(
            subject=subject,
            sender=app.config['MAIL_USERNAME'],
            recipients=[recipient]
        )
        msg.body = template
        mail.send(msg)
        return True, None
    except Exception as e:
        return False, str(e)

def send_async_email(app, msg):
    with app.app_context():
        mail.send(msg)

def create_notification(package_id, event_type, recipient, message, notification_type='email'):
    """Create a notification record"""
    notification = Notification(
        package_id=package_id,
        notification_type=notification_type,
        event_type=event_type,
        recipient=recipient,
        message=message,
        status='pending'
    )
    db.session.add(notification)
    db.session.commit()
    return notification

def generate_qr_code(data):
    """Generate QR code for tracking link"""
    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=5
    )
    qr.add_data(data)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to base64
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    
    return img_str


def geocode_address(address):
    """Return (lat, lng) for an address string using Google Geocoding API, or (None, None)."""
    if not address:
        return None, None
    api_key = app.config.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        return None, None
    try:
        resp = requests.get('https://maps.googleapis.com/maps/api/geocode/json', params={'address': address, 'key': api_key}, timeout=6)
        resp.raise_for_status()
        j = resp.json()
        if j and j.get('status') == 'OK' and j.get('results'):
            loc = j['results'][0]['geometry']['location']
            return float(loc.get('lat')), float(loc.get('lng'))
    except Exception as e:
        app.logger.debug('Geocode failed for %s: %s', address, e)
    return None, None

def format_dt(value, fmt='%Y-%m-%d %H:%M:%S'):
    """Safely format datetimes or ISO datetime strings for templates."""
    if not value:
        return ''
    # If it's already a datetime-like object
    if hasattr(value, 'strftime'):
        try:
            return value.strftime(fmt)
        except Exception:
            return str(value)

    # If it's a string, try to parse ISO or common formats
    if isinstance(value, str):
        try:
            dt = date_parser.parse(value)
            return dt.strftime(fmt)
        except Exception:
            return value

    # Fallback
    try:
        return str(value)
    except Exception:
        return ''

# ==================== Decorators ====================

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # If the request expects JSON (AJAX), return JSON errors instead of redirects
        wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                     request.is_json or \
                     request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']

        if not current_user.is_authenticated:
            if wants_json:
                return jsonify({'error': 'authentication_required'}), 401
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        if current_user.role not in ['admin', 'super_admin']:
            if wants_json:
                return jsonify({'error': 'forbidden'}), 403
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def permission_required(permission):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                         request.is_json or \
                         request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']

            if not current_user.is_authenticated:
                if wants_json:
                    return jsonify({'error': 'authentication_required'}), 401
                flash('Please log in to access this page.', 'warning')
                return redirect(url_for('login'))
            if not current_user.has_permission(permission):
                if wants_json:
                    return jsonify({'error': 'permission_denied'}), 403
                flash(f'You need {permission} permission to perform this action.', 'danger')
                return redirect(url_for('admin_dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ==================== User Loader ====================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# In-memory map of socket sid -> tab_token (optional tracking)
_SID_TO_TOKEN = {}


@socketio.on('connect')
def on_socket_connect():
    # Socket.IO query params are available on request.args
    try:
        tab_token = request.args.get('tab_token')
        if tab_token:
            st = SessionToken.query.filter_by(token=tab_token).first()
            if st:
                st.last_seen = datetime.utcnow()
                db.session.commit()
                # join a room per-user+token so server can target specific tab
                room = f'user_{st.user_id}_{st.token}'
                join_room(room)
                _SID_TO_TOKEN[request.sid] = tab_token
                app.logger.debug('Socket connected for user %s tab %s', st.user_id, st.token)
                return
    except Exception:
        app.logger.debug('Socket connect: no valid tab token provided')
    # If no tab token, allow anonymous socket but don't join per-user room
    app.logger.debug('Socket connected without tab token: %s', request.sid)


@socketio.on('disconnect')
def on_socket_disconnect():
    try:
        sid = request.sid
        token = _SID_TO_TOKEN.pop(sid, None)
        if token:
            # Optionally mark last_seen
            st = SessionToken.query.filter_by(token=token).first()
            if st:
                st.last_seen = datetime.utcnow()
                db.session.commit()
                app.logger.debug('Socket disconnected for token %s', token)
    except Exception:
        pass

# ==================== Context Processors ====================

@app.context_processor
def utility_processor():
    return {
        'now': datetime.utcnow(),
        'calculate_shipping_cost': calculate_shipping_cost,
        'generate_qr_code': generate_qr_code
    }

# Register Jinja filters for safe datetime formatting
app.jinja_env.filters['format_dt'] = format_dt
app.jinja_env.filters['format_date'] = format_dt
app.jinja_env.filters['format_datetime'] = format_dt
def parse_dt(value):
    """Parse a value into a datetime object for template comparisons."""
    if not value:
        return None
    if hasattr(value, 'year') and hasattr(value, 'month'):
        return value
    if isinstance(value, str):
        try:
            return date_parser.parse(value)
        except Exception:
            return None
    try:
        return date_parser.parse(str(value))
    except Exception:
        return None

# Make parse_dt available in templates
app.jinja_env.globals['parse_dt'] = parse_dt
# expose gettext as `_` in templates
app.jinja_env.globals.update(_=gettext)


def get_locale():
    # Prefer explicit session/cookie choice, fall back to best match from Accept-Language
    lang = session.get('lang') or session.get('language') or request.cookies.get('lang')
    if lang:
        return lang
    return request.accept_languages.best_match(app.config.get('BABEL_SUPPORTED_LOCALES', ['en']))

# Register the locale selector with Flask-Babel in a backward/forward-compatible way.
try:
    # Older/newer Flask-Babel versions expose different registration helpers
    if hasattr(babel, 'localeselector'):
        babel.localeselector(get_locale)
    elif hasattr(babel, 'locale_selector'):
        babel.locale_selector(get_locale)
    else:
        # Fallback: some versions use attribute assignment
        setattr(babel, 'locale_selector_func', get_locale)
except Exception:
    app.logger.warning('Could not register locale selector with Flask-Babel; falling back to Accept-Language')

# Sanitize enum-backed columns on startup to handle legacy/invalid values
def _sanitize_package_statuses():
    allowed = {'pending','shipped','dispatched','in_transit','out_for_delivery','on_hold','delivered','cancelled','returned'}
    try:
        with app.app_context():
            # Find distinct statuses present in DB
            rows = db.session.execute(text("SELECT DISTINCT status FROM packages")).fetchall()
            for (s,) in rows:
                if s and s not in allowed:
                    app.logger.warning('Sanitizing unknown package.status value "%s" -> "in_transit"', s)
                    db.session.execute(text("UPDATE packages SET status = :new WHERE status = :old"), {'new': 'in_transit', 'old': s})
            db.session.commit()
    except Exception as e:
        app.logger.debug('Status sanitization skipped: %s', e)

# Run sanitization at import time (safe no-op if DB not available)
_sanitize_package_statuses()

# ==================== Public Routes ====================

@app.route('/')
def index():
    # Get statistics for homepage
    stats = _get_homepage_stats()
    return render_template('index.html', stats=stats)


# Simple TTL cache for homepage stats to avoid hitting the DB on every request
_homepage_cache = {
    'value': None,
    'expires_at': 0
}

def _get_homepage_stats(ttl=15):
    """Return cached homepage stats; refresh every `ttl` seconds."""
    now = time.time()
    if _homepage_cache['value'] and _homepage_cache['expires_at'] > now:
        return _homepage_cache['value']

    # Recompute stats
    total_packages = Package.query.count()
    active_shipments = Package.query.filter_by(status='in_transit').count()
    delivered = Package.query.filter_by(status='delivered').count()
    stats = {
        'packages_delivered': delivered,
        'active_shipments': active_shipments,
        'happy_customers': User.query.count() * 10,
        'coverage': 45
    }

    _homepage_cache['value'] = stats
    _homepage_cache['expires_at'] = now + ttl
    return stats

@app.route('/about')
def about():
    return render_template('public/about.html')


@app.route('/services')
def services():
    return render_template('public/services.html')


@app.route('/careers')
def careers():
    return render_template('public/careers.html')

@app.route('/privacy')
def privacy():
    return render_template('public/privacy.html')

@app.route('/security')
def security():
    return render_template('public/security.html')

# Placeholder footer pages (press, investor relations, legal, terms)
@app.route('/press')
def press():
    return render_template('public/press.html')

@app.route('/investor-relations')
def investor_relations():
    return render_template('public/investor_relations.html')

@app.route('/legal')
def legal():
    return render_template('public/legal.html')

@app.route('/terms')
def terms():
    return render_template('public/terms.html')


@app.route('/contact')
def contact():
    """Public contact endpoint — redirect to privacy page contact anchor if present."""
    try:
        return redirect(url_for('privacy') + '#contact')
    except Exception:
        return redirect(url_for('index'))

@app.route('/trackingpackage/<tracking_code>')
def track_package(tracking_code):
    # Normalize incoming tracking codes: URL-decode, strip whitespace and
    # uppercase/lowercase will be handled during DB lookup. Log the request
    # origin to help debug mobile-specific failures.
    try:
        raw_code = unquote_plus(str(tracking_code or ''))
    except Exception:
        raw_code = str(tracking_code or '')
    code = raw_code.strip()
    app.logger.info('Track request from %s for code: "%s"', request.remote_addr, code)

    # Try exact match first, then a case-insensitive lookup. If no package
    # is found, render the public tracking input page with a helpful message
    # instead of returning the default 404 page (improves mobile UX when
    # users paste links or mistype codes).
    package = Package.query.filter_by(tracking_code=code).first()
    if not package:
        try:
            package = Package.query.filter(func.lower(Package.tracking_code) == code.lower()).first()
        except Exception:
            package = None

    if not package:
        flash('Tracking number not found. Please check the code and try again.', 'warning')
        return render_template('tracking/input.html'), 200

    # Load a small recent location history to keep render fast
    location_history = package.location_history.limit(5).all()

    # Get active hold if any
    active_hold = package.hold_records.filter_by(hold_end=None).first()

    # The public tracking page uses an external QR image service in the template,
    # so avoid generating a server-side QR code on every request (saves CPU/time).
    return render_template('tracking/track.html', 
                         package=package,
                         location_history=location_history,
                         active_hold=active_hold)


@app.route('/trackingpackage', methods=['GET', 'POST'])
def track_input():
    """Render a simple tracking input page or redirect to the tracking view.
    GET: show the input form
    POST: read `tracking_code` from form and redirect to `/track/<tracking_code>`
    """
    # Support GET with a `code`, `q`, or `tracking_number` query param so links like
    # `/track?code=ABC123`, `/track?q=ABC123`, or `/track?tracking_number=ABC123`
    # continue to work across older and newer form variants.
    if request.method == 'GET':
        q = (request.args.get('tracking_code') or request.args.get('tracking_number') or request.args.get('code') or request.args.get('q') or '').strip()
        if q:
            return redirect(url_for('track_package', tracking_code=q))

    if request.method == 'POST':
        code = (request.form.get('tracking_code') or request.form.get('tracking_number') or '').strip()
        if not code:
            flash('Please enter a SHPE tracking number', 'warning')
            return render_template('tracking/input.html')
        return redirect(url_for('track_package', tracking_code=code))

    return render_template('tracking/input.html')


@app.route('/api/trackingpackage', methods=['GET'])
@app.route('/api/trackingpackage/<tracking_code>', methods=['GET'])
def api_track(tracking_code=None):
    """API debug endpoint: returns JSON about a tracking code for quick mobile tests.
    Supports `/api/track?code=XYZ` and `/api/track/XYZ`.
    """
    code = (tracking_code or request.args.get('code') or request.args.get('q') or '').strip()
    if not code:
        return jsonify(success=False, error='code query param or path required'), 400

    try:
        raw_code = unquote_plus(code)
    except Exception:
        raw_code = code
    code = raw_code.strip()

    app.logger.info('API track request from %s for code: "%s"', request.remote_addr, code)

    package = Package.query.filter_by(tracking_code=code).first()
    if not package:
        try:
            package = Package.query.filter(func.lower(Package.tracking_code) == code.lower()).first()
        except Exception:
            package = None

    if not package:
        return jsonify(success=False, found=False, code=code), 404

    # Minimal package payload for debugging
    payload = {
        'id': package.id,
        'tracking_code': package.tracking_code,
        'status': getattr(package, 'status', None),
        'estimated_delivery': getattr(package, 'estimated_delivery', None).isoformat() if getattr(package, 'estimated_delivery', None) else None,
    }
    return jsonify(success=True, found=True, package=payload)


@app.route('/admin/geocode', methods=['POST'])
@login_required
def admin_geocode():
    """Server-side geocode: accepts JSON { address: '...' } and returns { success, lat, lng }"""
    data = request.get_json(silent=True) or {}
    address = data.get('address') or data.get('query')
    if not address:
        return jsonify(success=False, error='address required'), 400

    api_key = app.config.get('GOOGLE_MAPS_API_KEY')
    if not api_key:
        return jsonify(success=False, error='Google Maps API key not configured'), 500

    try:
        resp = requests.get(
            'https://maps.googleapis.com/maps/api/geocode/json',
            params={'address': address, 'key': api_key},
            timeout=6
        )
        resp.raise_for_status()
        j = resp.json()
    except Exception as e:
        return jsonify(success=False, error='geocode request failed', detail=str(e)), 502

    if not j or j.get('status') != 'OK' or not j.get('results'):
        return jsonify(success=False, error='no_results', detail=j.get('status') if j else 'invalid_response'), 404

    result = j['results'][0]
    loc = result['geometry']['location']
    return jsonify(success=True, lat=loc.get('lat'), lng=loc.get('lng'), formatted_address=result.get('formatted_address'))

@app.route('/api/trackingpackage/<tracking_code>')
def api_track_package(tracking_code):
    package = Package.query.filter_by(tracking_code=tracking_code).first_or_404()
    
    # Get latest location history
    # Keep API payload small for initial fetch
    location_history = package.location_history.limit(5).all()
    
    # Get active hold if any
    active_hold = package.hold_records.filter_by(hold_end=None).first()
    
    # Calculate progress percentage
    progress = 0
    if package.estimated_delivery and package.created_at:
        total_duration = (package.estimated_delivery - package.created_at).total_seconds()
        elapsed = (datetime.utcnow() - package.created_at).total_seconds()
        if total_duration > 0:
            progress = min(100, (elapsed / total_duration) * 100)
    
    return jsonify({
        'success': True,
        'tracking_code': package.tracking_code,
        'status': package.status,
        'current_location': {
            'lat': package.current_location_lat,
            'lng': package.current_location_lng,
            'name': package.current_location_name
        } if package.current_location_lat else None,
        'estimated_delivery': (package.estimated_delivery.isoformat() + 'Z') if package.estimated_delivery else None,
        'actual_delivery': (package.actual_delivery.isoformat() + 'Z') if package.actual_delivery else None,
        'sender': {
            'name': package.sender_name,
            'address': package.sender_address,
            'city': package.sender_city,
            'state': package.sender_state,
            'zip': package.sender_zip
        },
        'recipient': {
            'name': package.recipient_name,
            'address': package.recipient_address,
            'city': package.recipient_city,
            'state': package.recipient_state,
            'zip': package.recipient_zip
        },
        'package_details': {
            'weight': package.weight,
            'type': package.package_type,
            'shipping_method': package.shipping_method,
            'shipping_type': package.shipping_type
        },
        'history': [{
            'lat': h.latitude,
            'lng': h.longitude,
            'location_name': h.location_name,
            'status': h.status,
            'event_type': h.event_type,
            'description': h.description,
            'timestamp': (h.created_at.isoformat() + 'Z') if h.created_at else None
        } for h in location_history],
        'on_hold': active_hold is not None,
        'hold_reason': active_hold.reason if active_hold else None,
        'progress': progress
    })

@app.route('/api/stats')
def api_stats():
    """API endpoint for homepage statistics"""
    delivered = Package.query.filter_by(status='delivered').count()
    active = Package.query.filter_by(status='in_transit').count()
    
    return jsonify({
        'packages_delivered': delivered,
        'active_shipments': active,
        'happy_customers': User.query.count() * 10,
        'coverage': 45
    })

@app.route('/subscribe', methods=['POST'])
def subscribe():
    """Handle homepage newsletter signups."""
    email = (request.form.get('email') or '').strip()

    if not email or '@' not in email or '.' not in email.rsplit('@', 1)[-1]:
        flash('Please enter a valid email address.', 'warning')
        return redirect(url_for('index'))

    # Intentionally lightweight: accept valid submissions without persisting them.
    flash('Thanks for subscribing! We will keep you updated.', 'success')
    return redirect(url_for('index'))

@app.route('/set-language/<lang>', methods=['POST', 'GET'])
def set_language(lang):
    """Set language preference. Returns JSON for XHR requests."""
    if lang not in ALLOWED_LANGUAGES:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify(success=False, message='Unsupported language'), 400
        flash('Unsupported language selected.', 'danger')
        return redirect(request.referrer or url_for('index'))

    # persist chosen language in session (support older key too)
    session['lang'] = lang
    session['language'] = lang

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        resp = jsonify(success=True, language=lang)
        resp.set_cookie('lang', lang, max_age=30*24*3600, httponly=False)
        return resp

    flash('Language updated.', 'success')
    resp = redirect(request.referrer or url_for('index'))
    resp.set_cookie('lang', lang, max_age=30*24*3600, httponly=False)
    return resp

# ==================== Admin Authentication Routes ====================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        # Already logged in — route users to the appropriate area based on role
        if current_user.role in ('admin', 'super_admin'):
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            user.last_ip = request.remote_addr
            db.session.commit()

            # Respect 'next' param first
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)

            # Redirect admins to admin dashboard, regular users to their area
            if user.role in ('admin', 'super_admin'):
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('user_dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('admin/login.html')


@app.route('/login', methods=['GET', 'POST'])
def login_alias():
    """Public `/login` alias that delegates to the existing `admin_login` view.
    Delegating keeps authentication logic in one place while allowing
    templates to point to `url_for('login')` for a friendly path.
    """
    # Delegate handling to the admin_login view function so GET/POST work
    return admin_login()


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Public registration for non-admin users (role='viewer'). After registering we log the user in and send them to the user dashboard."""
    if current_user.is_authenticated:
        if current_user.role in ('admin', 'super_admin'):
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('user_dashboard'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password')
        full_name = request.form.get('full_name')

        if not username or not email or not password:
            flash('Username, email and password are required.', 'danger')
            return render_template('public/register.html')

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return render_template('public/register.html')

        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return render_template('public/register.html')

        user = User(
            username=username,
            email=email,
            full_name=full_name,
            role='viewer',
            is_active=True
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        # Auto-login after registration
        login_user(user)
        user.last_login = datetime.utcnow()
        user.last_ip = request.remote_addr
        db.session.commit()

        flash('Registration successful! Welcome.', 'success')
        return redirect(url_for('user_dashboard'))

    return render_template('public/register.html')


# ===== Per-tab token-based login for client-only sessions =====
@app.route('/api/login_tab', methods=['POST'])
def api_login_tab():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password')
    if not username or not password:
        return jsonify(success=False, error='missing_credentials'), 400

    user = User.query.filter_by(username=username).first()
    if not user or not user.check_password(password) or not user.is_active:
        return jsonify(success=False, error='invalid_credentials'), 401

    # create a per-tab token
    token = uuid.uuid4().hex
    st = SessionToken(user_id=user.id, token=token)
    db.session.add(st)
    db.session.commit()

    return jsonify(success=True, token=token, user_id=user.id, username=user.username)


@app.route('/api/unregister_tab', methods=['POST'])
def api_unregister_tab():
    data = request.get_json(silent=True) or {}
    token = data.get('token') or request.get_data(as_text=True)
    if not token:
        return jsonify(success=False, error='missing_token'), 400
    try:
        # Accept raw JSON string from navigator.sendBeacon
        if isinstance(token, str) and token.startswith('{'):
            parsed = json.loads(token)
            token = parsed.get('token')
    except Exception:
        pass

    if not token:
        return jsonify(success=False, error='missing_token'), 400

    st = SessionToken.query.filter_by(token=token).first()
    if st:
        try:
            db.session.delete(st)
            db.session.commit()
        except Exception:
            db.session.rollback()
    return jsonify(success=True)


@app.route('/user/dashboard')
@login_required
def user_dashboard():
    """A simple user-facing dashboard. Non-admin users land here after login/registration."""
    # For now show basic recent packages for this user's email (if any)
    user_packages = Package.query.filter(
        (Package.recipient_email == current_user.email) | (Package.sender_email == current_user.email)
    ).order_by(Package.created_at.desc()).limit(10).all()

    return render_template('user/dashboard.html', packages=user_packages)

@app.route('/admin/logout')
@login_required
def admin_logout():
    return logout()


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """Generic logout endpoint used by UI and templates."""
    try:
        logout_user()
    except Exception:
        pass
    flash('You have been logged out successfully.', 'success')
    # Redirect users to the public homepage after logout for a friendlier UX
    return redirect(url_for('index'))

@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    # This should be restricted in production
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        full_name = request.form.get('full_name')
        
        # Check if user exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('admin_register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return redirect(url_for('admin_register'))
        
        # Create new user
        user = User(
            username=username,
            email=email,
            full_name=full_name,
            role='admin'
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('admin/register.html')

# ==================== Admin Dashboard Routes ====================

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    # Get statistics
    total_packages = Package.query.count()
    in_transit = Package.query.filter_by(status='in_transit').count()
    on_hold = Package.query.filter_by(status='on_hold').count()
    delivered = Package.query.filter_by(status='delivered').count()
    pending = Package.query.filter_by(status='pending').count()
    
    # Get recent packages
    recent_packages = Package.query.order_by(Package.created_at.desc()).limit(10).all()
    
    # Get packages by shipping method
    packages_by_method = db.session.query(
        Package.shipping_method, func.count(Package.id)
    ).group_by(Package.shipping_method).all()
    
    # Get recent notifications
    recent_notifications = Notification.query.order_by(Notification.sent_at.desc()).limit(5).all()
    
    # Get on-time rate
    total_delivered = delivered
    on_time = Package.query.filter(
        Package.status == 'delivered',
        Package.actual_delivery <= Package.estimated_delivery
    ).count()
    on_time_rate = (on_time / total_delivered * 100) if total_delivered > 0 else 0
    
    # Get hold frequency this month
    hold_frequency = HoldRecord.query.filter(
        HoldRecord.hold_start >= datetime.now().replace(day=1)
    ).count()
    
    return render_template('admin/dashboard.html',
                         total_packages=total_packages,
                         in_transit=in_transit,
                         on_hold=on_hold,
                         delivered=delivered,
                         pending=pending,
                         recent_packages=recent_packages,
                         packages_by_method=packages_by_method,
                         recent_notifications=recent_notifications,
                         on_time_rate=round(on_time_rate, 1),
                         hold_frequency=hold_frequency)

@app.route('/admin/create-package', methods=['GET', 'POST'])
@login_required
@admin_required
@permission_required('create')
def create_package():
    if request.method == 'POST':
        try:
            # Generate unique tracking code
            tracking_code = generate_tracking_code()
            
            # Calculate estimated delivery (can be overridden by form input)
            estimated_delivery = calculate_delivery_date(
                request.form.get('shipping_method'),
                request.form.get('shipping_type')
            )

            # If admin provided an explicit estimated_delivery in the form, use it
            est_form = request.form.get('estimated_delivery')
            if est_form:
                try:
                    # Expect YYYY-MM-DD
                    est_parsed = datetime.strptime(est_form, '%Y-%m-%d')
                    estimated_delivery = est_parsed
                except Exception:
                    flash('Invalid estimated delivery date format. Use YYYY-MM-DD.', 'danger')
                    return render_template('admin/create-package.html')
            
            # Parse and validate weight
            weight_str = request.form.get('weight', '').strip()
            try:
                weight = float(weight_str)
            except (TypeError, ValueError):
                flash('Weight is required and must be a number.', 'danger')
                return render_template('admin/create-package.html')

            # Parse dimensions (accept separators: x, X, *, ×, space, comma)
            dimensions_str = request.form.get('dimensions', '').strip()
            length = width = height = None
            if dimensions_str:
                parts = re.split(r'[xX\*×,\s]+', dimensions_str)
                try:
                    length = float(parts[0]) if len(parts) > 0 and parts[0] else None
                    width = float(parts[1]) if len(parts) > 1 and parts[1] else None
                    height = float(parts[2]) if len(parts) > 2 and parts[2] else None
                except (TypeError, ValueError):
                    flash('Invalid dimensions format. Use LxWxH (e.g. 34x3x5).', 'danger')
                    return render_template('admin/create-package.html')
            
            # Create new package
            package = Package(
                tracking_code=tracking_code,
                sender_name=request.form.get('sender_name'),
                sender_company=request.form.get('sender_company'),
                sender_address=request.form.get('sender_address'),
                sender_address2=request.form.get('sender_address2'),
                sender_city=request.form.get('sender_city'),
                sender_state=request.form.get('sender_state'),
                sender_zip=request.form.get('sender_zip'),
                sender_country=request.form.get('sender_country', 'USA'),
                sender_phone=request.form.get('sender_phone'),
                sender_email=request.form.get('sender_email'),
                
                recipient_name=request.form.get('recipient_name'),
                recipient_company=request.form.get('recipient_company'),
                recipient_address=request.form.get('recipient_address'),
                recipient_address2=request.form.get('recipient_address2'),
                recipient_city=request.form.get('recipient_city'),
                recipient_state=request.form.get('recipient_state'),
                recipient_zip=request.form.get('recipient_zip'),
                recipient_country=request.form.get('recipient_country', 'USA'),
                recipient_phone=request.form.get('recipient_phone'),
                recipient_email=request.form.get('recipient_email'),
                
                weight=float(request.form.get('weight')),
                length=length,
                width=width,
                height=height,
                declared_value=float(request.form.get('declared_value')) if request.form.get('declared_value') else None,
                package_type=request.form.get('package_type'),
                shipping_type=request.form.get('shipping_type'),
                shipping_method=request.form.get('shipping_method'),
                insurance_required=request.form.get('insurance') == 'on',
                signature_required=request.form.get('signature_required') == 'on',
                
                estimated_delivery=estimated_delivery,
                pickup_address=request.form.get('pickup_address'),
                destination_address=request.form.get('destination_address'),
                
                status='pending',
                created_by=current_user.id,
                notes=request.form.get('notes')
            )
            
            db.session.add(package)
            db.session.commit()

            # Server-side geocode pickup and destination (if available) and persist
            try:
                if package.pickup_address and (package.pickup_lat is None or package.pickup_lng is None):
                    lat, lng = geocode_address(package.pickup_address)
                    if lat is not None and lng is not None:
                        package.pickup_lat = lat
                        package.pickup_lng = lng
                if package.destination_address and (package.destination_lat is None or package.destination_lng is None):
                    lat2, lng2 = geocode_address(package.destination_address)
                    if lat2 is not None and lng2 is not None:
                        package.destination_lat = lat2
                        package.destination_lng = lng2
                db.session.commit()
            except Exception:
                db.session.rollback()
            
            # Create initial location history
            history = LocationHistory(
                package_id=package.id,
                latitude=0,
                longitude=0,
                location_name='Package created',
                event_type='created',
                description='Package created in system',
                created_by=current_user.id
            )
            db.session.add(history)
            
            # Create notification
            tracking_url = url_for('track_package', tracking_code=tracking_code, _external=True)
            message = f"""
Your package has been created with tracking code: {tracking_code}
Track your package here: {tracking_url}
Thank you for choosing ShipTrack Pro!
            """
            
            notification = Notification(
                package_id=package.id,
                notification_type='email',
                event_type='tracking_created',
                recipient=package.recipient_email,
                subject=f'Package Tracking Code: {tracking_code}',
                message=message,
                status='sent'
            )
            db.session.add(notification)
            
            db.session.commit()
            
            flash(f'Package created successfully! Tracking Code: {tracking_code}', 'success')
            return redirect(url_for('view_package', package_id=package.id))
            
        except Exception as e:
            flash(f'Error creating package: {str(e)}', 'danger')
            db.session.rollback()
    
    return render_template('admin/create-package.html')

@app.route('/admin/packages')
@login_required
@admin_required
def manage_packages():
    # Get filter parameters
    status = request.args.get('status', 'all')
    search = request.args.get('search', '')
    
    query = Package.query
    
    if status != 'all':
        query = query.filter_by(status=status)
    
    if search:
        query = query.filter(
            db.or_(
                Package.tracking_code.like(f'%{search}%'),
                Package.recipient_name.like(f'%{search}%'),
                Package.sender_name.like(f'%{search}%')
            )
        )
    
    packages = query.order_by(Package.created_at.desc()).all()
    
    # Get counts for filter badges
    status_counts = {
        'all': Package.query.count(),
        'pending': Package.query.filter_by(status='pending').count(),
        'shipped': Package.query.filter_by(status='shipped').count(),
        'dispatched': Package.query.filter_by(status='dispatched').count(),
        'in_transit': Package.query.filter_by(status='in_transit').count(),
        'out_for_delivery': Package.query.filter_by(status='out_for_delivery').count(),
        'on_hold': Package.query.filter_by(status='on_hold').count(),
        'delivered': Package.query.filter_by(status='delivered').count(),
        'cancelled': Package.query.filter_by(status='cancelled').count(),
        'returned': Package.query.filter_by(status='returned').count()
    }
    
    return render_template('admin/manage-packages.html', 
                         packages=packages,
                         status_counts=status_counts,
                         current_status=status,
                         search=search)

@app.route('/admin/package/<int:package_id>')
@login_required
@admin_required
def view_package(package_id):
    package = Package.query.get_or_404(package_id)
    
    # Get location history
    location_history = package.location_history.limit(50).all()
    
    # Get hold records
    hold_records = package.hold_records.all()
    
    # Get notifications
    notifications = package.notifications.order_by(Notification.sent_at.desc()).limit(20).all()
    
    # Compute next milestone and approximate ETA to it (for display when admin reconnects)
    next_location_name = None
    eta_to_next = None
    try:
        next_milestone = package.milestones.filter(RouteMilestone.is_completed == False).order_by(RouteMilestone.sequence_order).first()
    except Exception:
        next_milestone = None

    if next_milestone:
        next_location_name = next_milestone.location_name
        # choose current coords or pickup as starting point
        cur_lat = package.current_location_lat if package.current_location_lat is not None else (package.pickup_lat or 0.0)
        cur_lng = package.current_location_lng if package.current_location_lng is not None else (package.pickup_lng or 0.0)
        try:
            rem_km = _haversine_km(cur_lat, cur_lng, float(next_milestone.latitude), float(next_milestone.longitude))
            # assume default speed 60 km/h for ETA computation when not actively simulating
            default_speed = 60.0
            eta_to_next = int(round((rem_km / max(0.0001, default_speed)) * 3600))
        except Exception:
            eta_to_next = None

    # Format a friendly next-eta text for initial render
    next_eta_text = None
    if next_location_name:
        if eta_to_next is None:
            next_eta_text = f'To {next_location_name}: calculating...'
        else:
            s = int(eta_to_next)
            if s <= 0:
                next_eta_text = f'To {next_location_name}: arriving shortly'
            else:
                hours = s // 3600
                minutes = (s % 3600) // 60
                parts = []
                if hours > 0:
                    parts.append(f"{hours}h")
                if minutes > 0:
                    parts.append(f"{minutes}m")
                if not parts:
                    parts.append('<1m')
                next_eta_text = f"To {next_location_name}: {' '.join(parts)}"

    return render_template('admin/view-package.html', 
                         package=package,
                         location_history=location_history,
                         hold_records=hold_records,
                         notifications=notifications,
                         next_location_name=next_location_name,
                         eta_to_next=eta_to_next,
                         next_eta_text=next_eta_text)

@app.route('/admin/package/<int:package_id>/update-location', methods=['POST'])
@login_required
@admin_required
@permission_required('update_location')
def update_location(package_id):
    package = Package.query.get_or_404(package_id)
    
    if package.status == 'on_hold':
        return jsonify({'error': 'Package is on hold'}), 400
    
    data = request.get_json()
    
    # Update current location only when provided
    if data.get('lat') not in (None, ''):
        package.current_location_lat = data.get('lat')
    if data.get('lng') not in (None, ''):
        package.current_location_lng = data.get('lng')
    if data.get('location_name'):
        package.current_location_name = data.get('location_name')
    # Update status (both current_status and canonical status)
    # Normalize incoming status values to the accepted enum values to avoid
    # SQLAlchemy LookupError when legacy values (e.g. 'departed') are present.
    incoming_status = data.get('status', package.status) or ''
    if isinstance(incoming_status, str):
        incoming_status = incoming_status.strip().lower()

    # Map legacy or vendor-specific statuses to canonical values
    status_map = {
        'departed': 'dispatched',
        'arrived': 'in_transit'
    }

    allowed_statuses = {'pending', 'shipped', 'dispatched', 'in_transit', 'out_for_delivery', 'on_hold', 'delivered', 'cancelled', 'returned'}

    normalized = status_map.get(incoming_status, incoming_status)
    if normalized not in allowed_statuses:
        app.logger.warning("Unknown status '%s' for package %s - normalizing to 'in_transit'", incoming_status, package.tracking_code)
        normalized = 'in_transit'

    package.current_status = normalized
    package.status = normalized
    
    # Add to location history. Ensure non-null latitude/longitude for DB schema.
    hist_lat = None
    hist_lng = None
    try:
        if data.get('lat') not in (None, ''):
            hist_lat = float(data.get('lat'))
        else:
            hist_lat = package.current_location_lat if package.current_location_lat is not None else 0.0
    except Exception:
        hist_lat = package.current_location_lat if package.current_location_lat is not None else 0.0

    try:
        if data.get('lng') not in (None, ''):
            hist_lng = float(data.get('lng'))
        else:
            hist_lng = package.current_location_lng if package.current_location_lng is not None else 0.0
    except Exception:
        hist_lng = package.current_location_lng if package.current_location_lng is not None else 0.0

    history = LocationHistory(
        package_id=package.id,
        latitude=hist_lat,
        longitude=hist_lng,
        location_name=data.get('location_name'),
        address=data.get('address'),
        status=package.current_status,
        event_type='location_update',
        description=data.get('description', 'Location updated'),
        created_by=current_user.id
    )
    
    db.session.add(history)
    
    # Create notification if requested
    if data.get('notify_recipient', False):
        message = f"Your package {package.tracking_code} is now at {data.get('location_name')}"
        notification = Notification(
            package_id=package.id,
            notification_type='email',
            event_type='location_update',
            recipient=package.recipient_email,
            subject=f'Location Update: {package.tracking_code}',
            message=message,
            status='sent'
        )
        db.session.add(notification)
    
    db.session.commit()
    
    # Emit real-time update
    socketio.emit('location_update', {
        'tracking_code': package.tracking_code,
        'lat': data.get('lat'),
        'lng': data.get('lng'),
        'location_name': data.get('location_name'),
        'status': package.current_status
    }, room=package.tracking_code)
    
    return jsonify({'success': True})

@app.route('/admin/package/<int:package_id>/hold', methods=['POST'])
@login_required
@admin_required
@permission_required('hold')
def place_on_hold(package_id):
    package = Package.query.get_or_404(package_id)
    
    data = request.get_json()
    reason = data.get('reason')
    category = data.get('category', 'other')
    description = data.get('description', '')
    # Optional hold location provided by admin
    hold_lat = data.get('lat')
    hold_lng = data.get('lng')
    hold_location_name = data.get('location_name')
    
    if not reason:
        return jsonify({'error': 'Reason required'}), 400
    
    # Create hold record
    # If coords not provided but a hold location name was given, try to geocode it
    if (hold_lat in (None, '')) and (hold_lng in (None, '')) and hold_location_name:
        try:
            glat, glng = geocode_address(hold_location_name)
            if glat is not None and glng is not None:
                hold_lat = glat
                hold_lng = glng
        except Exception:
            pass

    hold = HoldRecord(
        package_id=package.id,
        reason=reason,
        reason_category=category,
        description=description,
        created_by=current_user.id,
        hold_lat=float(hold_lat) if hold_lat not in (None, '') else None,
        hold_lng=float(hold_lng) if hold_lng not in (None, '') else None,
        hold_location_name=hold_location_name
    )
    
    # Update package status
    package.status = 'on_hold'
    # If admin provided coordinates for hold, update package current location to match
    try:
        if hold.hold_lat not in (None, '') and hold.hold_lng not in (None, ''):
            package.current_location_lat = float(hold.hold_lat)
            package.current_location_lng = float(hold.hold_lng)
            if hold_location_name:
                package.current_location_name = hold_location_name
            else:
                package.current_location_name = 'On Hold Location'
    except Exception:
        pass
    
    # Add to location history
    history = LocationHistory(
        package_id=package.id,
        latitude=package.current_location_lat if package.current_location_lat is not None else (float(hold.hold_lat) if hold.hold_lat not in (None, '') else 0),
        longitude=package.current_location_lng if package.current_location_lng is not None else (float(hold.hold_lng) if hold.hold_lng not in (None, '') else 0),
        location_name=package.current_location_name or hold_location_name,
        status='on_hold',
        event_type='hold',
        description=f'Package placed on hold: {reason}',
        created_by=current_user.id
    )
    
    db.session.add(hold)
    db.session.add(history)
    
    # Create notification if requested
    if data.get('notify', True):
        message = f"Your package {package.tracking_code} has been placed on hold. Reason: {reason}"
        notification = Notification(
            package_id=package.id,
            notification_type='email',
            event_type='on_hold',
            recipient=package.recipient_email,
            subject=f'Package On Hold: {package.tracking_code}',
            message=message,
            status='sent'
        )
        db.session.add(notification)
    
    db.session.commit()
    
    # Emit status update
    payload = {
        'tracking_code': package.tracking_code,
        'status': 'on_hold',
        'reason': reason
    }
    # include hold location when present
    if hold.hold_lat is not None and hold.hold_lng is not None:
        payload.update({'hold_lat': hold.hold_lat, 'hold_lng': hold.hold_lng, 'hold_location_name': hold.hold_location_name})

    socketio.emit('status_update', payload, room=package.tracking_code)
    
    return jsonify({'success': True})

@app.route('/admin/package/<int:package_id>/release-hold', methods=['POST'])
@login_required
@admin_required
@permission_required('hold')
def release_hold(package_id):
    package = Package.query.get_or_404(package_id)
    
    # Find active hold
    active_hold = package.hold_records.filter_by(hold_end=None).first()
    
    if active_hold:
        active_hold.hold_end = datetime.utcnow()
        active_hold.resolved_by = current_user.id
        active_hold.resolved_at = datetime.utcnow()
        
        package.status = 'in_transit'
        
        # Add to location history
        history = LocationHistory(
            package_id=package.id,
            latitude=package.current_location_lat or 0,
            longitude=package.current_location_lng or 0,
            location_name=package.current_location_name,
            status='in_transit',
            event_type='hold_released',
            description='Hold released, package back in transit',
            created_by=current_user.id
        )
        db.session.add(history)
        
        # Create notification
        notification = Notification(
            package_id=package.id,
            notification_type='email',
            event_type='hold_released',
            recipient=package.recipient_email,
            subject=f'Hold Released: {package.tracking_code}',
            message=f'Your package {package.tracking_code} is back in transit.',
            status='sent'
        )
        db.session.add(notification)
        
        db.session.commit()
        
        # Emit status update
        socketio.emit('status_update', {
            'tracking_code': package.tracking_code,
            'status': 'in_transit'
        }, room=package.tracking_code)
    
    return jsonify({'success': True})

@app.route('/admin/package/<int:package_id>/deliver', methods=['POST'])
@login_required
@admin_required
def mark_delivered(package_id):
    package = Package.query.get_or_404(package_id)
    
    data = request.get_json()
    
    package.status = 'delivered'
    package.actual_delivery = datetime.utcnow()
    
    # Create delivery confirmation
    confirmation = DeliveryConfirmation(
        package_id=package.id,
        received_by=data.get('received_by', package.recipient_name),
        relationship=data.get('relationship', 'Recipient'),
        signature_data=data.get('signature'),
        delivery_notes=data.get('notes'),
        confirmed_by=current_user.id,
        latitude=data.get('lat'),
        longitude=data.get('lng')
    )
    
    # Add to location history
    history = LocationHistory(
        package_id=package.id,
        latitude=data.get('lat', package.current_location_lat or 0),
        longitude=data.get('lng', package.current_location_lng or 0),
        location_name='Delivered',
        status='delivered',
        event_type='delivered',
        description='Package delivered successfully',
        created_by=current_user.id
    )
    
    db.session.add(confirmation)
    db.session.add(history)
    
    # Create notification
    notification = Notification(
        package_id=package.id,
        notification_type='email',
        event_type='delivered',
        recipient=package.recipient_email,
        subject=f'Package Delivered: {package.tracking_code}',
        message=f'Your package {package.tracking_code} has been delivered successfully.',
        status='sent'
    )
    db.session.add(notification)
    
    db.session.commit()
    
    # Emit status update
    socketio.emit('status_update', {
        'tracking_code': package.tracking_code,
        'status': 'delivered'
    }, room=package.tracking_code)
    
    return jsonify({'success': True})


@app.route('/admin/package/<int:package_id>/status', methods=['POST'])
@login_required
@admin_required
@permission_required('edit')
def set_package_status(package_id):
    """Set a package's status (admin) and optionally notify recipient."""
    package = Package.query.get_or_404(package_id)

    # Accept JSON or form data
    data = request.get_json(silent=True) or request.form
    status = data.get('status')
    notify = data.get('notify', True)
    # normalize notify from form string
    if isinstance(notify, str):
        notify = notify.lower() in ['1', 'true', 'yes', 'on']

    allowed_statuses = ['pending', 'shipped', 'dispatched', 'in_transit', 'out_for_delivery', 'on_hold', 'delivered', 'cancelled', 'returned']

    if not status or status not in allowed_statuses:
        return jsonify({'error': 'Invalid status'}), 400

    previous = package.status
    package.status = status

    # If marking delivered, set actual_delivery
    if status == 'delivered' and not package.actual_delivery:
        package.actual_delivery = datetime.utcnow()

    # Record a status update in location history for audit
    history = LocationHistory(
        package_id=package.id,
        latitude=package.current_location_lat or 0,
        longitude=package.current_location_lng or 0,
        location_name=status.replace('_', ' ').title(),
        status=status,
        event_type='status_update',
        description=f'Status changed from {previous} to {status}',
        created_by=current_user.id
    )

    db.session.add(history)

    # Create notification if requested
    if notify and package.recipient_email:
        subject = f'Package Status Update: {package.tracking_code}'
        message = data.get('message') or f'Your package {package.tracking_code} status is now "{status.replace("_", " ").title()}".'

        notification = Notification(
            package_id=package.id,
            notification_type='email',
            event_type='status_update',
            recipient=package.recipient_email,
            subject=subject,
            message=message,
            status='sent'
        )
        db.session.add(notification)

    db.session.commit()

    # Emit real-time status update
    socketio.emit('status_update', {
        'tracking_code': package.tracking_code,
        'status': package.status,
        'previous': previous
    }, room=package.tracking_code)

    # Return JSON if called via API, otherwise redirect back
    if request.is_json:
        return jsonify({'success': True, 'status': package.status})
    flash(f'Package status updated to {package.status}', 'success')
    return redirect(url_for('view_package', package_id=package.id))


def _haversine_km(lat1, lon1, lat2, lon2):
    """Return distance in kilometers between two lat/lon points."""
    # convert decimal degrees to radians
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat/2)**2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    km = 6371 * c
    return km


def _simulate_route_worker(package_id, dest_lat, dest_lng, speed_kmh=60, interval=5):
    with app.app_context():
        pkg = Package.query.get(package_id)
        if not pkg:
            return

        # Determine start coordinates
        start_lat = pkg.current_location_lat if pkg.current_location_lat is not None else (pkg.pickup_lat or 0.0)
        start_lng = pkg.current_location_lng if pkg.current_location_lng is not None else (pkg.pickup_lng or 0.0)

        total_km = _haversine_km(start_lat, start_lng, float(dest_lat), float(dest_lng))
        if total_km <= 0:
            # Already at destination
            pkg.current_location_lat = float(dest_lat)
            pkg.current_location_lng = float(dest_lng)
            pkg.current_location_name = 'Destination'
            pkg.status = 'delivered'
            pkg.actual_delivery = datetime.utcnow()
            db.session.add(pkg)
            db.session.commit()
            socketio.emit('status_update', {'tracking_code': pkg.tracking_code, 'status': 'delivered'}, room=pkg.tracking_code)
            return

        # Calculate distance per step (km)
        step_km = max(0.001, speed_kmh * (interval / 3600.0))
        steps = max(1, int(math.ceil(total_km / step_km)))

        # Move in linear interpolation steps
        for i in range(steps):
            # Refresh package state to pick up any hold changes
            pkg = Package.query.get(package_id)
            if not pkg:
                return
            # If package placed on hold, stop simulation
            if pkg.status == 'on_hold':
                # stop simulation when put on hold
                socketio.emit('status_update', {'tracking_code': pkg.tracking_code, 'status': 'on_hold'}, room=pkg.tracking_code)
                return

            # Stop if simulation flag was cleared by admin
            if hasattr(pkg, 'simulation_active') and not pkg.simulation_active:
                socketio.emit('status_update', {'tracking_code': pkg.tracking_code, 'status': 'simulation_paused'}, room=pkg.tracking_code)
                return

            frac = float(i + 1) / steps
            new_lat = start_lat + (float(dest_lat) - start_lat) * frac
            new_lng = start_lng + (float(dest_lng) - start_lng) * frac

            pkg.current_location_lat = new_lat
            pkg.current_location_lng = new_lng
            pkg.current_location_name = f'In Transit ({int(frac*100)}%)'
            pkg.status = 'in_transit'
            db.session.add(pkg)

            history = LocationHistory(
                package_id=pkg.id,
                latitude=new_lat,
                longitude=new_lng,
                location_name=pkg.current_location_name,
                address=None,
                status=pkg.status,
                event_type='location_update',
                description=f'Automatic simulated movement ({int(frac*100)}%)',
                created_by=pkg.created_by
            )
            db.session.add(history)
            db.session.commit()

            # Determine next milestone (if any) and ETA to it
            try:
                next_milestone = pkg.milestones.filter(RouteMilestone.is_completed == False).order_by(RouteMilestone.sequence_order).first()
            except Exception:
                next_milestone = None

            next_name = None
            eta_to_next = None
            if next_milestone:
                next_name = next_milestone.location_name
                try:
                    rem_to_next = _haversine_km(new_lat, new_lng, float(next_milestone.latitude), float(next_milestone.longitude))
                    eta_to_next = int(round((rem_to_next / max(0.0001, speed_kmh)) * 3600))
                except Exception:
                    eta_to_next = None

            socketio.emit('location_update', {
                'tracking_code': pkg.tracking_code,
                'lat': new_lat,
                'lng': new_lng,
                'location_name': pkg.current_location_name,
                'status': pkg.status,
                'progress': int(frac * 100),
                'remaining_km': round(_haversine_km(new_lat, new_lng, float(dest_lat), float(dest_lng)), 3),
                'eta_seconds': int(round((_haversine_km(new_lat, new_lng, float(dest_lat), float(dest_lng)) / max(0.0001, speed_kmh)) * 3600)),
                'next_location_name': next_name,
                'eta_to_next': eta_to_next
            }, room=pkg.tracking_code)

            time.sleep(interval)

        # Arrived at destination
        pkg = Package.query.get(package_id)
        if not pkg:
            return
        if pkg.status != 'on_hold':
            # clear active simulation flag
            if hasattr(pkg, 'simulation_active'):
                pkg.simulation_active = False
            pkg.current_location_lat = float(dest_lat)
            pkg.current_location_lng = float(dest_lng)
            pkg.current_location_name = 'Delivered Location'
            pkg.status = 'delivered'
            pkg.actual_delivery = datetime.utcnow()
            db.session.add(pkg)

            history = LocationHistory(
                package_id=pkg.id,
                latitude=float(dest_lat),
                longitude=float(dest_lng),
                location_name='Delivered Location',
                address=None,
                status='delivered',
                event_type='delivered',
                description='Arrived at destination (simulated)',
                created_by=pkg.created_by
            )
            db.session.add(history)

            # Create notification
            if pkg.recipient_email:
                notification = Notification(
                    package_id=pkg.id,
                    notification_type='email',
                    event_type='delivered',
                    recipient=pkg.recipient_email,
                    subject=f'Package Delivered: {pkg.tracking_code}',
                    message=f'Your package {pkg.tracking_code} has been delivered (simulated).',
                    status='sent'
                )
                db.session.add(notification)

            db.session.commit()

            socketio.emit('location_update', {
                'tracking_code': pkg.tracking_code,
                'lat': pkg.current_location_lat,
                'lng': pkg.current_location_lng,
                'location_name': pkg.current_location_name,
                'status': pkg.status,
                'progress': 100,
                'remaining_km': 0.0,
                'eta_seconds': 0,
                'next_location_name': None,
                'eta_to_next': 0
            }, room=pkg.tracking_code)
            socketio.emit('status_update', {'tracking_code': pkg.tracking_code, 'status': 'delivered'}, room=pkg.tracking_code)


@app.route('/admin/package/<int:package_id>/simulate-route', methods=['POST'])
@login_required
@admin_required
@permission_required('update_location')
def simulate_route(package_id):
    """Start background simulation moving package toward provided lat/lng.
    JSON body: { lat: float, lng: float, speed_kmh: optional, interval: optional_seconds }
    """
    data = request.get_json(silent=True) or {}
    try:
        dest_lat = float(data.get('lat'))
        dest_lng = float(data.get('lng'))
    except Exception:
        return jsonify({'error': 'Invalid destination coordinates'}), 400

    speed = float(data.get('speed_kmh', 60))
    interval = int(data.get('interval', 5))

    # Persist simulation parameters on the package so ETA can be recomputed later
    package = Package.query.get(package_id)
    if package:
        package.simulation_active = True
        package.simulation_speed_kmh = speed
        package.simulation_interval = interval
        package.simulation_started_at = datetime.utcnow()
        package.status = 'in_transit'
        db.session.add(package)
        db.session.commit()

    # Launch background thread
    t = Thread(target=_simulate_route_worker, args=(package_id, dest_lat, dest_lng, speed, interval), daemon=True)
    t.start()

    return jsonify({'success': True, 'message': 'Route simulation started'})


@app.route('/admin/package/<int:package_id>/stop-simulation', methods=['POST'])
@login_required
@admin_required
@permission_required('hold')
def stop_simulation(package_id):
    """Stop a running simulation by placing the package on hold (admin action)."""
    package = Package.query.get_or_404(package_id)

    reason = request.get_json(silent=True, force=True) if request.is_json else request.form
    reason_text = None
    if isinstance(reason, dict):
        reason_text = reason.get('reason')
    else:
        reason_text = request.form.get('reason')

    if not reason_text:
        reason_text = 'Simulation stopped by admin'

    # Create hold record
    hold = HoldRecord(
        package_id=package.id,
        reason=reason_text,
        reason_category='operational',
        description='Stopped simulation',
        created_by=current_user.id
    )
    package.status = 'on_hold'
    # Also clear any active simulation flag
    if hasattr(package, 'simulation_active'):
        package.simulation_active = False

    history = LocationHistory(
        package_id=package.id,
        latitude=package.current_location_lat or 0,
        longitude=package.current_location_lng or 0,
        location_name=package.current_location_name,
        status='on_hold',
        event_type='hold',
        description=reason_text,
        created_by=current_user.id
    )

    db.session.add(hold)
    db.session.add(history)

    # Notify recipient optionally
    if package.recipient_email:
        notification = Notification(
            package_id=package.id,
            notification_type='email',
            event_type='on_hold',
            recipient=package.recipient_email,
            subject=f'Package On Hold: {package.tracking_code}',
            message=f'Your package {package.tracking_code} has been placed on hold. Reason: {reason_text}',
            status='sent'
        )
        db.session.add(notification)

    db.session.commit()

    socketio.emit('status_update', {
        'tracking_code': package.tracking_code,
        'status': 'on_hold',
        'reason': reason_text
    }, room=package.tracking_code)

    return jsonify({'success': True, 'message': 'Simulation stopped and package placed on hold'})


@app.route('/admin/package/<int:package_id>/set-simulation', methods=['POST'])
@login_required
@admin_required
@permission_required('update_location')
def set_simulation_state(package_id):
    """Toggle persisted simulation_active flag without creating a hold record.
    JSON body: { active: true|false, speed_kmh: optional }
    """
    package = Package.query.get_or_404(package_id)
    data = request.get_json(silent=True) or {}
    active = data.get('active')
    speed = data.get('speed_kmh')

    if active is None:
        return jsonify({'error': 'Missing "active" boolean field'}), 400

    # Normalize boolean
    if isinstance(active, str):
        active = active.lower() in ['1', 'true', 'yes', 'on']

    package.simulation_active = bool(active)
    if speed is not None:
        try:
            package.simulation_speed_kmh = float(speed)
        except Exception:
            pass

    if package.simulation_active:
        package.simulation_started_at = datetime.utcnow()

    db.session.add(package)
    db.session.commit()

    socketio.emit('status_update', {
        'tracking_code': package.tracking_code,
        'status': 'in_transit' if package.simulation_active else 'simulation_paused'
    }, room=package.tracking_code)

    return jsonify({'success': True, 'simulation_active': package.simulation_active})


@app.route('/admin/package/<int:package_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@permission_required('edit')
def edit_package(package_id):
    package = Package.query.get_or_404(package_id)

    if request.method == 'POST':
        try:
            # Update sender
            package.sender_name = request.form.get('sender_name')
            package.sender_company = request.form.get('sender_company')
            package.sender_address = request.form.get('sender_address')
            package.sender_address2 = request.form.get('sender_address2')
            package.sender_city = request.form.get('sender_city')
            package.sender_state = request.form.get('sender_state')
            package.sender_zip = request.form.get('sender_zip')
            package.sender_country = request.form.get('sender_country')
            package.sender_phone = request.form.get('sender_phone')
            package.sender_email = request.form.get('sender_email')

            # Update recipient
            package.recipient_name = request.form.get('recipient_name')
            package.recipient_company = request.form.get('recipient_company')
            package.recipient_address = request.form.get('recipient_address')
            package.recipient_address2 = request.form.get('recipient_address2')
            package.recipient_city = request.form.get('recipient_city')
            package.recipient_state = request.form.get('recipient_state')
            package.recipient_zip = request.form.get('recipient_zip')
            package.recipient_country = request.form.get('recipient_country')
            package.recipient_phone = request.form.get('recipient_phone')
            package.recipient_email = request.form.get('recipient_email')

            # Update package details
            weight = request.form.get('weight')
            try:
                package.weight = float(weight) if weight else package.weight
            except Exception:
                flash('Invalid weight value', 'danger')
                return redirect(url_for('edit_package', package_id=package.id))

            dimensions_str = request.form.get('dimensions', '').strip()
            if dimensions_str:
                parts = re.split(r'[xX\*×,\s]+', dimensions_str)
                try:
                    package.length = float(parts[0]) if len(parts) > 0 and parts[0] else package.length
                    package.width = float(parts[1]) if len(parts) > 1 and parts[1] else package.width
                    package.height = float(parts[2]) if len(parts) > 2 and parts[2] else package.height
                except Exception:
                    flash('Invalid dimensions format. Use LxWxH (e.g. 34x3x5).', 'danger')
                    return redirect(url_for('edit_package', package_id=package.id))

            declared = request.form.get('declared_value')
            package.declared_value = float(declared) if declared else package.declared_value
            package.package_type = request.form.get('package_type') or package.package_type
            package.shipping_type = request.form.get('shipping_type') or package.shipping_type
            package.shipping_method = request.form.get('shipping_method') or package.shipping_method
            package.insurance_required = request.form.get('insurance') == 'on'
            package.signature_required = request.form.get('signature_required') == 'on'

            package.pickup_address = request.form.get('pickup_address') or package.pickup_address
            package.destination_address = request.form.get('destination_address') or package.destination_address
            package.notes = request.form.get('notes')

            # Optional: allow admin to set status
            new_status = request.form.get('status')
            if new_status:
                package.status = new_status

            # Optional: allow admin to set estimated delivery date (YYYY-MM-DD)
            est = request.form.get('estimated_delivery')
            if est:
                try:
                    # Parse date and set time to midnight UTC
                    parsed = datetime.strptime(est, '%Y-%m-%d')
                    package.estimated_delivery = parsed
                except Exception:
                    flash('Invalid estimated delivery date format. Use YYYY-MM-DD.', 'danger')
                    return redirect(url_for('edit_package', package_id=package.id))

            # If addresses changed, attempt to geocode and persist coordinates
            try:
                if package.pickup_address:
                    lat, lng = geocode_address(package.pickup_address)
                    if lat is not None and lng is not None:
                        package.pickup_lat = lat
                        package.pickup_lng = lng
                if package.destination_address:
                    dlat, dlng = geocode_address(package.destination_address)
                    if dlat is not None and dlng is not None:
                        package.destination_lat = dlat
                        package.destination_lng = dlng
            except Exception:
                app.logger.debug('Geocode during edit failed')

            db.session.commit()
            flash('Package updated successfully', 'success')
            return redirect(url_for('view_package', package_id=package.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating package: {str(e)}', 'danger')

    # Render edit form
    return render_template('admin/edit-package.html', package=package)

@app.route('/admin/package/<int:package_id>/milestones', methods=['POST'])
@login_required
@admin_required
def set_milestones(package_id):
    package = Package.query.get_or_404(package_id)
    
    data = request.get_json()
    milestones = data.get('milestones', [])
    
    # Clear existing milestones
    package.milestones.delete()
    
    # Add new milestones
    start_time = datetime.utcnow()
    
    for i, milestone in enumerate(milestones):
        estimated_hours = milestone.get('estimated_hours', 0)
        estimated_arrival = start_time + timedelta(hours=estimated_hours) if estimated_hours > 0 else None
        
        new_milestone = RouteMilestone(
            package_id=package.id,
            sequence_order=i,
            location_name=milestone.get('location_name'),
            location_type=milestone.get('location_type', 'waypoint'),
            latitude=milestone.get('lat'),
            longitude=milestone.get('lng'),
            estimated_hours_from_start=estimated_hours,
            estimated_arrival=estimated_arrival,
            notes=milestone.get('notes')
        )
        db.session.add(new_milestone)
    
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/admin/package/<int:package_id>/label')
@login_required
@admin_required
def generate_label(package_id):
    package = Package.query.get_or_404(package_id)
    
    # Create a formatted shipping label
    label_content = f"""
{'='*60}
                    SHIPPING LABEL
{'='*60}

TRACKING NUMBER: {package.tracking_code}
{'='*60}

FROM:
{package.sender_name}
{package.sender_company if package.sender_company else ''}
{package.sender_address}
{package.sender_address2 if package.sender_address2 else ''}
{package.sender_city}, {package.sender_state} {package.sender_zip}
{package.sender_country}
Tel: {package.sender_phone}
Email: {package.sender_email}

{'='*60}

TO:
{package.recipient_name}
{package.recipient_company if package.recipient_company else ''}
{package.recipient_address}
{package.recipient_address2 if package.recipient_address2 else ''}
{package.recipient_city}, {package.recipient_state} {package.recipient_zip}
{package.recipient_country}
Tel: {package.recipient_phone}
Email: {package.recipient_email}

{'='*60}

PACKAGE DETAILS:
Weight: {package.weight} kg
Dimensions: {package.length or '?'} x {package.width or '?'} x {package.height or '?'} {package.dimensions_unit}
Package Type: {package.package_type.upper()}
Shipping Method: {package.shipping_method.upper()} - {package.shipping_type.upper()}
Declared Value: {package.currency} {package.declared_value if package.declared_value else '0.00'}
Insurance: {'YES' if package.insurance_required else 'NO'}
Signature Required: {'YES' if package.signature_required else 'NO'}

Estimated Delivery: {package.estimated_delivery.strftime('%Y-%m-%d') if package.estimated_delivery else 'N/A'}

{'='*60}
Created: {package.created_at.strftime('%Y-%m-%d %H:%M:%S')}
Status: {package.status.upper()}

Please ensure this label is clearly visible on the package.
Thank you for choosing ShipTrack Pro!
{'='*60}
    """
    
    response = make_response(label_content)
    response.headers["Content-Type"] = "text/plain"
    response.headers["Content-Disposition"] = f"attachment; filename=shipping_label_{package.tracking_code}.txt"
    return response

@app.route('/admin/package/<int:package_id>/receipt')
@login_required
@admin_required
def generate_receipt(package_id):
    package = Package.query.get_or_404(package_id)
    
    # Calculate cost
    cost = calculate_shipping_cost(
        package.weight,
        package.package_type,
        package.shipping_method,
        package.shipping_type,
        package.declared_value or 0
    )
    
    receipt_content = f"""
{'='*60}
                    SHIPPING RECEIPT
{'='*60}

Receipt Number: RCP-{package.tracking_code}
Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{'='*60}

TRACKING INFORMATION:
Tracking Code: {package.tracking_code}
Status: {package.status.upper()}
Created: {package.created_at.strftime('%Y-%m-%d %H:%M:%S')}

{'='*60}

SHIPMENT DETAILS:
From: {package.sender_name}
To: {package.recipient_name}

Package Type: {package.package_type.upper()}
Weight: {package.weight} kg
Shipping Method: {package.shipping_method.upper()} - {package.shipping_type.upper()}

{'='*60}

CHARGES:
Base Shipping: ${cost * 0.7:.2f}
Fuel Surcharge: ${cost * 0.1:.2f}
Handling Fee: ${cost * 0.1:.2f}
{'Insurance: $' + f'{package.declared_value * 0.01:.2f}' if package.insurance_required and package.declared_value else ''}
{'='*30}
TOTAL: ${cost:.2f}

{'='*60}

Thank you for using ShipTrack Pro!
For questions, contact support@shiptrackpro.com
{'='*60}
    """
    
    response = make_response(receipt_content)
    response.headers["Content-Type"] = "text/plain"
    response.headers["Content-Disposition"] = f"attachment; filename=receipt_{package.tracking_code}.txt"
    return response

@app.route('/admin/analytics')
@login_required
@admin_required
def analytics():
    # Get current date and time
    now = datetime.utcnow()
    
    # Basic counts
    total_packages = Package.query.count()
    active_shipments = Package.query.filter_by(status='in_transit').count()
    on_hold = Package.query.filter_by(status='on_hold').count()
    delivered = Package.query.filter_by(status='delivered').count()
    
    # Packages by status
    packages_by_status = db.session.query(
        Package.status, func.count(Package.id)
    ).group_by(Package.status).all()
    
    # Packages by shipping method
    packages_by_method = db.session.query(
        Package.shipping_method, func.count(Package.id)
    ).group_by(Package.shipping_method).all()
    
    # Packages by type
    packages_by_type = db.session.query(
        Package.package_type, func.count(Package.id)
    ).group_by(Package.package_type).all()
    
    # Daily data for last 30 days
    thirty_days_ago = now - timedelta(days=30)
    daily_data = []
    current_date = thirty_days_ago.date()
    
    while current_date <= now.date():
        next_date = current_date + timedelta(days=1)
        shipments = Package.query.filter(
            Package.created_at >= current_date,
            Package.created_at < next_date
        ).count()
        
        deliveries = Package.query.filter(
            Package.actual_delivery >= current_date,
            Package.actual_delivery < next_date
        ).count()
        
        holds = HoldRecord.query.filter(
            HoldRecord.hold_start >= current_date,
            HoldRecord.hold_start < next_date
        ).count()
        
        daily_data.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'shipments': shipments,
            'deliveries': deliveries,
            'holds': holds
        })
        
        current_date = next_date
    
    # Hold reasons
    hold_reasons = db.session.query(
        HoldRecord.reason_category, func.count(HoldRecord.id)
    ).group_by(HoldRecord.reason_category).all()
    
    # Delivery performance
    delivered_packages = Package.query.filter_by(status='delivered').all()
    on_time_count = 0
    delayed_count = 0
    early_count = 0
    
    for package in delivered_packages:
        if package.actual_delivery and package.estimated_delivery:
            if package.actual_delivery <= package.estimated_delivery:
                on_time_count += 1
            elif package.actual_delivery > package.estimated_delivery:
                delayed_count += 1
            else:
                early_count += 1
    
    total_delivered = len(delivered_packages)
    on_time_rate = (on_time_count / total_delivered * 100) if total_delivered > 0 else 0
    delay_rate = (delayed_count / total_delivered * 100) if total_delivered > 0 else 0
    early_rate = (early_count / total_delivered * 100) if total_delivered > 0 else 0
    
    # Value statistics
    total_value = db.session.query(func.sum(Package.declared_value)).scalar() or 0
    avg_value = total_value / total_packages if total_packages > 0 else 0
    max_value = db.session.query(func.max(Package.declared_value)).scalar() or 0
    insured_count = Package.query.filter_by(insurance_required=True).count()
    
    # Weight distribution
    light_count = Package.query.filter(Package.weight < 2).count()
    medium_count = Package.query.filter(Package.weight.between(2, 10)).count()
    heavy_count = Package.query.filter(Package.weight > 10).count()
    
    # Geographic distribution
    domestic_count = Package.query.filter_by(sender_country='USA').filter_by(recipient_country='USA').count()
    international_count = total_packages - domestic_count
    
    # Trends (simplified - compare with previous period)
    last_month = now - timedelta(days=30)
    previous_count = Package.query.filter(Package.created_at < last_month).count()
    total_packages_trend = ((total_packages - previous_count) / previous_count * 100) if previous_count > 0 else 0
    
    # Recent packages
    recent_packages = Package.query.order_by(Package.created_at.desc()).limit(10).all()
    
    # Status colors for legend
    status_colors = ['#4e73df', '#1cc88a', '#f6c23e', '#e74a3b', '#36b9cc', '#858796']
    
    return render_template('admin/analytics.html',
                         total_packages=total_packages,
                         active_shipments=active_shipments,
                         on_hold=on_hold,
                         delivered=delivered,
                         packages_by_status=packages_by_status,
                         packages_by_method=packages_by_method,
                         packages_by_type=packages_by_type,
                         daily_data=[(d['date'], d['shipments'], d['deliveries'], d['holds']) for d in daily_data],
                         hold_reasons=hold_reasons,
                         on_time_count=on_time_count,
                         delayed_count=delayed_count,
                         early_count=early_count,
                         on_time_rate=round(on_time_rate, 1),
                         delay_rate=round(delay_rate, 1),
                         early_rate=round(early_rate, 1),
                         total_value=total_value,
                         avg_value=avg_value,
                         max_value=max_value,
                         insured_count=insured_count,
                         light_count=light_count,
                         medium_count=medium_count,
                         heavy_count=heavy_count,
                         domestic_count=domestic_count,
                         international_count=international_count,
                         total_packages_trend=round(total_packages_trend, 1),
                         on_time_trend=5,  # These would need actual trend calculation
                         avg_time_trend=-2,
                         active_trend=0,
                         recent_packages=recent_packages,
                         status_colors=status_colors,
                         now=now)

@app.route('/admin/analytics/data')
@login_required
@admin_required
def analytics_data():
    period = request.args.get('period', 'month')
    now = datetime.utcnow()
    
    # Set date range based on period
    if period == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start_date = now - timedelta(days=7)
    elif period == 'month':
        start_date = now - timedelta(days=30)
    elif period == 'quarter':
        start_date = now - timedelta(days=90)
    elif period == 'year':
        start_date = now - timedelta(days=365)
    else:
        start_date = datetime(2000, 1, 1)  # All time
    
    # Query data for the period
    packages = Package.query.filter(Package.created_at >= start_date).all()
    
    # Compile statistics
    stats = {
        'total': len(packages),
        'by_status': {},
        'by_method': {},
        'by_type': {},
        'daily': {}
    }
    
    for package in packages:
        # Status counts
        stats['by_status'][package.status] = stats['by_status'].get(package.status, 0) + 1
        
        # Method counts
        stats['by_method'][package.shipping_method] = stats['by_method'].get(package.shipping_method, 0) + 1
        
        # Type counts
        stats['by_type'][package.package_type] = stats['by_type'].get(package.package_type, 0) + 1
        
        # Daily counts
        day = package.created_at.strftime('%Y-%m-%d')
        if day not in stats['daily']:
            stats['daily'][day] = 0
        stats['daily'][day] += 1
    
    return jsonify(stats)

@app.route('/admin/bulk-import', methods=['POST'])
@login_required
@admin_required
@permission_required('create')
def bulk_import():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif file.filename.endswith(('.xls', '.xlsx')):
            df = pd.read_excel(file)
        else:
            return jsonify({'error': 'Invalid file format. Please upload CSV or Excel file.'}), 400
        
        success_count = 0
        errors = []
        
        for index, row in df.iterrows():
            try:
                tracking_code = generate_tracking_code()
                estimated_delivery = calculate_delivery_date(
                    row.get('shipping_method', 'ground'),
                    row.get('shipping_type', 'standard')
                )
                
                package = Package(
                    tracking_code=tracking_code,
                    sender_name=row['sender_name'],
                    sender_address=row['sender_address'],
                    sender_phone=row['sender_phone'],
                    sender_email=row['sender_email'],
                    recipient_name=row['recipient_name'],
                    recipient_address=row['recipient_address'],
                    recipient_phone=row['recipient_phone'],
                    recipient_email=row['recipient_email'],
                    weight=float(row['weight']),
                    dimensions=row.get('dimensions'),
                    declared_value=float(row.get('declared_value', 0)),
                    package_type=row.get('package_type', 'general'),
                    shipping_type=row.get('shipping_type', 'standard'),
                    shipping_method=row.get('shipping_method', 'ground'),
                    estimated_delivery=estimated_delivery,
                    pickup_address=row['pickup_address'],
                    destination_address=row['destination_address'],
                    created_by=current_user.id
                )
                
                db.session.add(package)
                success_count += 1
                
            except Exception as e:
                errors.append(f"Row {index + 2}: {str(e)}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'imported': success_count,
            'errors': errors
        })
        
    except Exception as e:
        return jsonify({'error': f'Error processing file: {str(e)}'}), 400

@app.route('/admin/export/packages')
@login_required
@admin_required
@permission_required('export')
def export_packages():
    # Get all packages
    packages = Package.query.all()
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write headers
    writer.writerow(['Tracking Code', 'Sender Name', 'Sender Email', 'Recipient Name', 
                    'Recipient Email', 'Weight', 'Package Type', 'Shipping Method',
                    'Status', 'Created At', 'Estimated Delivery', 'Actual Delivery'])
    
    # Write data
    for pkg in packages:
        writer.writerow([
            pkg.tracking_code,
            pkg.sender_name,
            pkg.sender_email,
            pkg.recipient_name,
            pkg.recipient_email,
            pkg.weight,
            pkg.package_type,
            f"{pkg.shipping_method} - {pkg.shipping_type}",
            pkg.status,
            pkg.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            pkg.estimated_delivery.strftime('%Y-%m-%d') if pkg.estimated_delivery else '',
            pkg.actual_delivery.strftime('%Y-%m-%d') if pkg.actual_delivery else ''
        ])
    
    # Create response
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = f"attachment; filename=packages_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    response.headers["Content-type"] = "text/csv"
    
    return response

@app.route('/admin/users')
@login_required
@admin_required
def manage_users():
    if current_user.role not in ['admin', 'super_admin']:
        flash('Only admins can manage users.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    users = User.query.all()
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/create', methods=['POST'])
@login_required
@admin_required
def create_user():
    if current_user.role != 'super_admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already exists'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already exists'}), 400
    
    user = User(
        username=data['username'],
        email=data['email'],
        full_name=data['full_name'],
        role=data['role'],
        department=data.get('department'),
        phone=data.get('phone'),
        is_active=True
    )
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_user(user_id):
    if current_user.role != 'super_admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()
    
    return jsonify({'success': True, 'is_active': user.is_active})


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_edit_user(user_id):
    if current_user.role not in ['admin', 'super_admin']:
        flash('Only admins can edit users.', 'danger')
        return redirect(url_for('admin_dashboard'))

    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        # Update editable fields available to admin-level accounts.
        user.email = request.form.get('email') or user.email
        user.full_name = request.form.get('full_name') or user.full_name
        user.department = request.form.get('department') or user.department
        user.phone = request.form.get('phone') or user.phone

        # Only super admins can change sensitive role/password settings.
        if current_user.role == 'super_admin':
            role = request.form.get('role')
            if role in ['admin', 'super_admin', 'viewer']:
                user.role = role

            new_password = request.form.get('password')
            if new_password:
                try:
                    user.set_password(new_password)
                except Exception:
                    flash('Failed to set new password', 'danger')
        elif request.form.get('role') or request.form.get('password'):
            flash('Only super admins can change a user role or password.', 'warning')

        try:
            db.session.add(user)
            db.session.commit()
            flash('User updated successfully', 'success')
            return redirect(url_for('manage_users'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating user: {str(e)}', 'danger')

    return render_template('admin/edit-user.html', user=user)


@app.route('/admin/users/<int:user_id>/delete')
@login_required
@admin_required
def admin_delete_user(user_id):
    if current_user.role != 'super_admin':
        flash('Only super admins can delete users.', 'danger')
        return redirect(url_for('admin_dashboard'))

    user = User.query.get_or_404(user_id)
    # Prevent accidental removal of default admin
    if user.username == 'admin':
        flash('Cannot delete built-in admin user.', 'danger')
        return redirect(url_for('manage_users'))

    try:
        db.session.delete(user)
        db.session.commit()
        flash('User deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting user: {str(e)}', 'danger')

    return redirect(url_for('manage_users'))

# ==================== API Routes ====================

@app.route('/api/calculate-shipping', methods=['POST'])
def api_calculate_shipping():
    data = request.get_json()
    
    cost = calculate_shipping_cost(
        data.get('weight', 1),
        data.get('package_type', 'general'),
        data.get('shipping_method', 'ground'),
        data.get('shipping_type', 'standard'),
        data.get('declared_value', 0)
    )
    
    return jsonify({
        'success': True,
        'cost': cost,
        'currency': 'USD'
    })

@app.route('/api/validate-tracking', methods=['POST'])
def api_validate_tracking():
    data = request.get_json()
    code = data.get('tracking_code', '').upper()
    
    package = Package.query.filter_by(tracking_code=code).first()
    
    return jsonify({
        'valid': package is not None,
        'exists': package is not None
    })


# Register a browser tab/session token for the current logged-in user
@app.route('/api/register_tab', methods=['POST'])
def api_register_tab():
    if not current_user.is_authenticated:
        return jsonify({'error': 'authentication_required'}), 401
    data = request.get_json() or {}
    token = data.get('token')
    if not token:
        # create a token server-side if client didn't provide one
        token = uuid.uuid4().hex

    # Upsert token for this user
    st = SessionToken.query.filter_by(user_id=current_user.id, token=token).first()
    if not st:
        st = SessionToken(user_id=current_user.id, token=token)
        db.session.add(st)
    st.last_seen = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'token': token})


@app.route('/logout_tab', methods=['POST'])
def logout_tab():
    # Logout only the current tab (requires user to be authenticated via cookie)
    if not current_user.is_authenticated:
        return jsonify({'success': True})
    data = request.get_json() or {}
    token = data.get('token')
    if token:
        try:
            SessionToken.query.filter_by(user_id=current_user.id, token=token).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()
    # Do NOT call logout_user() here — we only invalidate the tab token
    return jsonify({'success': True})


@app.route('/logout_all', methods=['POST'])
@login_required
def logout_all():
    try:
        SessionToken.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
    # Also clear Flask login for the browser session
    logout_user()
    return jsonify({'success': True, 'redirect': url_for('index')})

# ==================== SocketIO Events ====================

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')

@socketio.on('subscribe')
def handle_subscribe(data):
    tracking_code = data.get('tracking_code')
    if tracking_code:
        room = f"tracking_{tracking_code}"
        join_room(room)
        print(f'Client {request.sid} subscribed to {room}')

@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    tracking_code = data.get('tracking_code')
    if tracking_code:
        room = f"tracking_{tracking_code}"
        leave_room(room)
        print(f'Client {request.sid} unsubscribed from {room}')

# ==================== Error Handlers ====================

@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500

@app.errorhandler(403)
def forbidden_error(error):
    return render_template('errors/403.html'), 403

# ==================== Create Admin User Function ====================

def create_admin_user():
    """Create default admin user if not exists"""
    with app.app_context():
        # Allow overriding defaults via environment variables (.env)
        default_username = os.environ.get('ADMIN_USERNAME')
        default_email = os.environ.get('ADMIN_EMAIL')
        default_password = os.environ.get('ADMIN_PASSWORD')

        admin = User.query.filter_by(username=default_username).first()
        if not admin:
            admin = User(
                username=default_username,
                email=default_email,
                full_name='System Administrator',
                role='super_admin',
                is_active=True
            )
            admin.set_password(default_password)
            db.session.add(admin)
            db.session.commit()
            print('✅ Admin user created successfully')
            print(f'   Username: {default_username}')
            print(f'   Password: {default_password}')
        else:
            print('✅ Admin user already exists')

# ==================== Run Application ====================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_admin_user()

    # Determine bind host and port (can be overridden by env vars)
    bind_host = os.environ.get('BIND_HOST', '0.0.0.0')
    try:
        bind_port = int(os.environ.get('PORT') or os.environ.get('FLASK_RUN_PORT') or 23709)
    except Exception:
        bind_port = 23709

    # Compute a friendly display host: prefer a routable IPv4 when binding to 0.0.0.0
    display_host = bind_host
    if bind_host in ('0.0.0.0', '::'):
        try:
            # Create a dummy UDP socket to determine the outbound IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('1.1.1.1', 80))
            display_host = s.getsockname()[0]
            s.close()
        except Exception:
            display_host = 'localhost'

    # Allow overriding the displayed public hostname
    override_host = os.environ.get('PUBLIC_HOSTNAME') or app.config.get('PUBLIC_HOSTNAME') or app.config.get('PUBLIC_HOST')
    printed_host = override_host if override_host else display_host

    debug = os.environ.get('FLASK_DEBUG', '0') in ('1', 'true', 'True')

    # Optionally hide detailed startup URLs from logs (set env var HIDE_STARTUP_URL=1)
    hide_startup = os.environ.get('HIDE_STARTUP_URL') in ('1', 'true', 'True') or app.config.get('HIDE_STARTUP_URL', False)
    if not hide_startup:
        print('\n' + '='*60)
        print('🚀 ShipTrack Pro is starting...')
        print('='*60)
        print('📱 Access the application:')
        print(f'   • Homepage: http://{printed_host}:{bind_port}')
        print(f'   • Admin Login: http://{printed_host}:{bind_port}/admin/login')
        print(f'   • Tracking: http://{printed_host}:{bind_port}/track/[CODE]')
        print('='*60 + '\n')

    socketio.run(app, host=bind_host, port=bind_port, debug=debug, allow_unsafe_werkzeug=True)