# wsgi.py
import sys
import os

# Add your project directory to the path
path = '/home/shpeexpress/mysite'
if path not in sys.path:
    sys.path.append(path)

from app import app as application  # Your Flask app

# Domain middleware removed so the app no longer rewrites HTTP_HOST for
# pythonanywhere.com. This makes running locally or behind other proxies
# simpler. Re-add middleware only if you need to force a specific host.