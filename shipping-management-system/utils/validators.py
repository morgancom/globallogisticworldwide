import re

def is_valid_tracking_number(tracking):
    return bool(re.match(r'^[A-Z0-9\-]+$', tracking))
