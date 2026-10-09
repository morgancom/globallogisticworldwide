import random
import string
from datetime import datetime, timedelta

def generate_tracking_code(length=20):
    """Generate a 20-digit alphanumeric tracking code"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choices(characters, k=length))

def calculate_delivery_date(shipping_method, shipping_type, start_date=None):
    """Calculate estimated delivery date based on shipping method and type"""
    if start_date is None:
        start_date = datetime.now()
    
    days_map = {
        'air': {'express': 1, 'standard': 3, 'freight': 5},
        'ground': {'express': 2, 'standard': 5, 'freight': 10},
        'sea': {'express': 7, 'standard': 14, 'freight': 21}
    }
    
    days = days_map.get(shipping_method, {}).get(shipping_type, 5)
    return start_date + timedelta(days=days)