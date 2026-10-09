from app import app, db, Package, User
from flask import url_for
from utils.generators import generate_tracking_code, calculate_delivery_date
from datetime import datetime, timedelta
import random
import os
import socket

def create_sample_packages():
    with app.app_context():
        # Check if admin exists
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            print("Please run the app first to create admin user")
            return
        
        # Sample package data
        sample_packages = [
            {
                'sender_name': 'Amazon Fulfillment',
                'sender_address': '123 Fulfillment Way, Seattle, WA 98101',
                'sender_phone': '206-123-4567',
                'sender_email': 'shipping@amazon.com',
                'recipient_name': 'John Smith',
                'recipient_address': '456 Oak Street, Apt 3B, San Francisco, CA 94105',
                'recipient_phone': '415-555-0123',
                'recipient_email': 'john.smith@email.com',
                'weight': 2.5,
                'dimensions': '30x20x15',
                'declared_value': 150.00,
                'package_type': 'electronics',
                'shipping_type': 'express',
                'shipping_method': 'air',
                'pickup_address': 'Amazon Fulfillment Center, Seattle, WA 98101',
                'destination_address': '456 Oak Street, Apt 3B, San Francisco, CA 94105',
                'status': 'in_transit'
            },
            {
                'sender_name': 'Walmart Distribution',
                'sender_address': '789 Logistics Blvd, Bentonville, AR 72712',
                'sender_phone': '479-555-6789',
                'sender_email': 'shipping@walmart.com',
                'recipient_name': 'Sarah Johnson',
                'recipient_address': '321 Pine Avenue, Dallas, TX 75201',
                'recipient_phone': '214-555-0456',
                'recipient_email': 'sarah.j@email.com',
                'weight': 15.0,
                'dimensions': '60x40x30',
                'declared_value': 350.00,
                'package_type': 'general',
                'shipping_type': 'standard',
                'shipping_method': 'ground',
                'pickup_address': 'Walmart DC, Bentonville, AR 72712',
                'destination_address': '321 Pine Avenue, Dallas, TX 75201',
                'status': 'in_transit'
            },
            {
                'sender_name': 'eBay Seller',
                'sender_address': '555 Market Street, Los Angeles, CA 90001',
                'sender_phone': '213-555-2345',
                'sender_email': 'seller@ebay.com',
                'recipient_name': 'Michael Chen',
                'recipient_address': '888 Broadway, New York, NY 10003',
                'recipient_phone': '212-555-0789',
                'recipient_email': 'm.chen@email.com',
                'weight': 0.8,
                'dimensions': '20x15x10',
                'declared_value': 75.00,
                'package_type': 'documents',
                'shipping_type': 'express',
                'shipping_method': 'air',
                'pickup_address': '555 Market Street, Los Angeles, CA 90001',
                'destination_address': '888 Broadway, New York, NY 10003',
                'status': 'pending'
            },
            {
                'sender_name': 'Best Buy',
                'sender_address': '777 Electronics Drive, Richfield, MN 55423',
                'sender_phone': '612-555-8901',
                'sender_email': 'shipping@bestbuy.com',
                'recipient_name': 'Emily Rodriguez',
                'recipient_address': '234 Beach Boulevard, Miami, FL 33139',
                'recipient_phone': '305-555-1234',
                'recipient_email': 'emily.r@email.com',
                'weight': 5.2,
                'dimensions': '40x30x20',
                'declared_value': 899.99,
                'package_type': 'electronics',
                'shipping_type': 'freight',
                'shipping_method': 'ground',
                'pickup_address': 'Best Buy DC, Richfield, MN 55423',
                'destination_address': '234 Beach Boulevard, Miami, FL 33139',
                'status': 'on_hold'
            },
            {
                'sender_name': 'Target',
                'sender_address': '999 Retail Circle, Minneapolis, MN 55403',
                'sender_phone': '612-555-4321',
                'sender_email': 'shipping@target.com',
                'recipient_name': 'David Wilson',
                'recipient_address': '567 Mountain View Rd, Denver, CO 80202',
                'recipient_phone': '303-555-5678',
                'recipient_email': 'd.wilson@email.com',
                'weight': 3.7,
                'dimensions': '35x25x20',
                'declared_value': 210.50,
                'package_type': 'general',
                'shipping_type': 'standard',
                'shipping_method': 'ground',
                'pickup_address': 'Target DC, Minneapolis, MN 55403',
                'destination_address': '567 Mountain View Rd, Denver, CO 80202',
                'status': 'delivered'
            }
        ]
        
        created_packages = []
        
        for pkg_data in sample_packages:
            # Generate unique tracking code
            tracking_code = generate_tracking_code()
            
            # Calculate estimated delivery
            estimated_delivery = calculate_delivery_date(
                pkg_data['shipping_method'],
                pkg_data['shipping_type']
            )
            
            # Adjust dates for variety
            if pkg_data['status'] == 'delivered':
                estimated_delivery = datetime.now() - timedelta(days=2)
            elif pkg_data['status'] == 'on_hold':
                estimated_delivery = datetime.now() + timedelta(days=3)
            
            # Create package
            package = Package(
                tracking_code=tracking_code,
                sender_name=pkg_data['sender_name'],
                sender_address=pkg_data['sender_address'],
                sender_phone=pkg_data['sender_phone'],
                sender_email=pkg_data['sender_email'],
                recipient_name=pkg_data['recipient_name'],
                recipient_address=pkg_data['recipient_address'],
                recipient_phone=pkg_data['recipient_phone'],
                recipient_email=pkg_data['recipient_email'],
                weight=pkg_data['weight'],
                dimensions=pkg_data['dimensions'],
                declared_value=pkg_data['declared_value'],
                package_type=pkg_data['package_type'],
                shipping_type=pkg_data['shipping_type'],
                shipping_method=pkg_data['shipping_method'],
                estimated_delivery=estimated_delivery,
                pickup_address=pkg_data['pickup_address'],
                destination_address=pkg_data['destination_address'],
                status=pkg_data['status'],
                created_by=admin.id
            )
            
            # Set location coordinates based on status
            if pkg_data['status'] == 'in_transit':
                # Somewhere in transit
                package.current_location_lat = 39.8283 + random.uniform(-5, 5)
                package.current_location_lng = -98.5795 + random.uniform(-10, 10)
            elif pkg_data['status'] == 'on_hold':
                # At customs or facility
                package.current_location_lat = 40.7128
                package.current_location_lng = -74.0060  # NYC (customs)
            elif pkg_data['status'] == 'delivered':
                # At destination
                package.current_location_lat = 39.7392
                package.current_location_lng = -104.9903  # Denver
            
            db.session.add(package)
            created_packages.append({
                'tracking_code': tracking_code,
                'recipient': pkg_data['recipient_name'],
                'status': pkg_data['status']
            })
        
        db.session.commit()
        
        print("\n" + "="*60)
        print("✅ SAMPLE PACKAGES CREATED SUCCESSFULLY!")
        print("="*60)
        print("\n📦 Use these tracking codes to test the system:\n")
        
        for pkg in created_packages:
            status_emoji = {
                'in_transit': '🚚',
                'pending': '⏳',
                'on_hold': '⏸️',
                'delivered': '✅'
            }.get(pkg['status'], '📦')
            
            print(f"{status_emoji} {pkg['tracking_code']} - {pkg['recipient']} ({pkg['status']})")
        
        # Build a friendly tracking URL using the machine IP when possible
        bind_host = os.environ.get('BIND_HOST', '0.0.0.0')
        try:
            bind_port = int(os.environ.get('PORT') or os.environ.get('FLASK_RUN_PORT') or 23709)
        except Exception:
            bind_port = 23709
        display_host = bind_host
        if bind_host in ('0.0.0.0', '::'):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(('1.1.1.1', 80))
                display_host = s.getsockname()[0]
                s.close()
            except Exception:
                display_host = 'localhost'

        print("\n" + "="*60)
        # Prefer generating an external URL via Flask if possible
        try:
            with app.app_context():
                sample_url = url_for('track_package', tracking_code='YOUR_CODE', _external=True)
        except Exception:
            sample_url = f"http://{display_host}:{bind_port}/track/YOUR_CODE"

        print(f"🌐 Access tracking at: {sample_url}")
        print("="*60)

if __name__ == '__main__':
    create_sample_packages()