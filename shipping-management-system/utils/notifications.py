from flask_mail import Message
from flask import url_for
from threading import Thread
from twilio.rest import Client
import os

def send_async_email(app, msg):
    with app.app_context():
        mail.send(msg)

def send_tracking_notification(app, mail, recipient_email, tracking_code, tracking_url):
    """Send tracking code via email"""
    msg = Message(
        subject="Your Package Tracking Information",
        sender=app.config['MAIL_USERNAME'],
        recipients=[recipient_email]
    )
    # Ensure a proper external tracking URL using Flask's url_for when not provided
    if not tracking_url:
        # Prefer generating an external URL via Flask if possible. When this
        # runs outside of a request context (background thread) `url_for`
        # may not be able to build a fully qualified URL unless
        # `SERVER_NAME`/`PREFERRED_URL_SCHEME` are set. As a robust fallback
        # use `PUBLIC_HOSTNAME` or `BASE_URL` from config so emails contain a
        # reachable link (especially important for mobile users).
        try:
            with app.app_context():
                tracking_url = url_for('track_package', tracking_code=tracking_code, _external=True)
        except Exception:
            host = app.config.get('PUBLIC_HOSTNAME') or app.config.get('BASE_URL') or ''
            tracking_url = f"{host.rstrip('/')}/track/{tracking_code}"

    msg.body = f"""
    Your package has been created with tracking code: {tracking_code}
    
    Track your package here: {tracking_url}
    
    You will receive updates as your package moves through our system.
    
    Thank you for choosing our shipping service!
    """
    
    Thread(target=send_async_email, args=(app, msg)).start()

def send_status_update(app, mail, recipient_email, tracking_code, status, location):
    """Send status update notification"""
    msg = Message(
        subject=f"Package Status Update: {tracking_code}",
        sender=app.config['MAIL_USERNAME'],
        recipients=[recipient_email]
    )
    # Build external tracking URL via url_for where possible
    try:
        with app.app_context():
            tracking_url = url_for('track_package', tracking_code=tracking_code, _external=True)
    except Exception:
        host = app.config.get('PUBLIC_HOSTNAME') or app.config.get('BASE_URL') or ''
        tracking_url = f"{host.rstrip('/')}/track/{tracking_code}"

    msg.body = f"""
    Your package ({tracking_code}) status has been updated.
    
    Current Status: {status}
    Current Location: {location}
    
    Track your package: {tracking_url}
    """
    
    Thread(target=send_async_email, args=(app, msg)).start()

def send_hold_notification(app, mail, recipient_email, tracking_code, reason):
    """Send notification when package is on hold"""
    msg = Message(
        subject=f"Package On Hold: {tracking_code}",
        sender=app.config['MAIL_USERNAME'],
        recipients=[recipient_email]
    )
    try:
        with app.app_context():
            tracking_url = url_for('track_package', tracking_code=tracking_code, _external=True)
    except Exception:
            host = app.config.get('PUBLIC_HOSTNAME') or app.config.get('BASE_URL') or ''
            tracking_url = f"{host.rstrip('/')}/track/{tracking_code}"

    msg.body = f"""
    Your package ({tracking_code}) is currently on hold.
    
    Reason: {reason}
    
    We will notify you once the package resumes movement.
    
    Track your package: {tracking_url}
    """
    
    Thread(target=send_async_email, args=(app, msg)).start()

# SMS notifications (optional - requires Twilio)
def send_sms_notification(phone_number, message):
    """Send SMS notification"""
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
    
    if account_sid and auth_token:
        client = Client(account_sid, auth_token)
        
        message = client.messages.create(
            body=message,
            from_=os.environ.get('TWILIO_PHONE_NUMBER'),
            to=phone_number
        )
        return message.sid
    return None