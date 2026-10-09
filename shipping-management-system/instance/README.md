Deployment / instance/.env instructions

1. Purpose
- Keep runtime secrets out of the repository. Copy `instance/.env.template` → `instance/.env` on the server and fill values.
- Ensure `instance/.env` is NOT checked into Git (add `instance/*.env` to `.gitignore` if needed).

2. Recommended production values
- `SECRET_KEY`: generate a strong random secret (e.g. `python -c "import secrets; print(secrets.token_hex(32))"`).
- `DATABASE_URL`: use a managed DB (Postgres/MySQL) with credentials stored in a secrets manager.
- `MAIL_PASSWORD`: use an app password or SMTP relay credentials.
- `SESSION_COOKIE_SECURE=1`, `REMEMBER_COOKIE_SECURE=1`, `PREFERRED_URL_SCHEME=https`, `ENABLE_HSTS=1`.

3. TLS
- Obtain a TLS certificate (Let’s Encrypt recommended). Configure your reverse proxy (nginx/Caddy/Traefik) to terminate TLS and forward `X-Forwarded-*` headers.
- Example nginx snippet (replace placeholders):

```nginx
server {
    listen 80;
    server_name example.com www.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name example.com;
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    location / {
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header Host $host;
        proxy_pass http://127.0.0.1:5000;
    }
}
```

4. Environment & process management
- Use a process manager (systemd, supervisor, or gunicorn with socket/proxy). Example systemd unit for Gunicorn + SocketIO (adjust paths):

```ini
[Unit]
Description=SHPE ShipTrack Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/path/to/shipping-management-system
EnvironmentFile=/path/to/shipping-management-system/instance/.env
ExecStart=/path/to/venv/bin/gunicorn -b 127.0.0.1:5000 wsgi:application --workers 3
Restart=always

[Install]
WantedBy=multi-user.target
```

5. Quick test (on server)
- Create `instance/.env` from template and fill values.
- Activate virtualenv and install requirements (if not done):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Run locally for smoke test (bind to 127.0.0.1 or unix socket):

```bash
export FLASK_APP=wsgi.py
export FLASK_ENV=production
source .venv/bin/activate
gunicorn -b 127.0.0.1:5000 wsgi:application
```

6. Security checklist
- Rotate any credentials found in the repo.
- Ensure `.env` / `instance/.env` is excluded from git.
- Enable HTTPS and set `SESSION_COOKIE_SECURE=1` and `ENABLE_HSTS=1` in production.
- Monitor logs for suspicious activity and enforce least privilege on DB and mail accounts.

7. Optional: use a secret manager
- For larger deployments, use AWS Secrets Manager, Azure Key Vault, HashiCorp Vault, or environment variables injected by your orchestrator.

If you want, I can:
- Add `instance/*.env` to `.gitignore` (I can patch `.gitignore` too).
- Create a systemd unit file template tuned to your environment.
- Scan the repo for remaining secrets and provide a remediation plan to purge them from git history.
