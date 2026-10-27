#!/bin/bash
# Serve a page that reads the secret at run time and connects to PostgreSQL.
# The unit file expands only the project id, the secret name, and the address.

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-psycopg2 nginx

install -d /opt/twotier
cat > /opt/twotier/app.py <<'PY'
import base64
import json
import os
import time
import urllib.request

import psycopg2
from http.server import BaseHTTPRequestHandler, HTTPServer

_cache = {"value": "", "at": 0.0}


def read_secret():
    now = time.time()
    if _cache["value"] and now - _cache["at"] < 60:
        return _cache["value"]
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        token = json.load(response)["access_token"]
    name = (
        "https://secretmanager.googleapis.com/v1/projects/"
        + os.environ["PROJECT_ID"]
        + "/secrets/"
        + os.environ["SECRET_NAME"]
        + "/versions/latest:access"
    )
    secret_request = urllib.request.Request(name, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(secret_request, timeout=10) as response:
        payload = json.load(response)["payload"]["data"]
    value = base64.b64decode(payload).decode()
    _cache["value"] = value
    _cache["at"] = now
    return value


def page():
    connection = psycopg2.connect(
        host=os.environ["DB_HOST"],
        dbname="appdb",
        user="webapp",
        password=read_secret(),
        connect_timeout=5,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
    finally:
        connection.close()
    return "database-connected\n" + version + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = page().encode()
            self.send_response(200)
        except Exception:
            body = b"database-unavailable\n"
            self.send_response(500)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
PY

cat > /etc/systemd/system/twotier-web.service <<'UNIT'
[Unit]
Description=Two-tier proof page
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/twotier/app.py
Environment=PROJECT_ID=__PROJECT_ID__
Environment=SECRET_NAME=__SECRET_NAME__
Environment=DB_HOST=__DB_ADDRESS__
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/nginx/sites-available/twotier <<'NGINX'
server {
    listen 80 default_server;
    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}
NGINX
ln -sfn /etc/nginx/sites-available/twotier /etc/nginx/sites-enabled/twotier
rm -f /etc/nginx/sites-enabled/default
systemctl daemon-reload
systemctl enable --now twotier-web
systemctl restart nginx
report_ready "WEB_READY"
