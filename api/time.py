"""Liveness is independent of external providers and NAS mount availability."""
import datetime
from flask import jsonify
from . import app, v1_bp


@app.get("/time")
@v1_bp.get("/time")
def get_time():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


@app.get("/healthz")
def health():
    return jsonify({"status": "ok", "service": "home-lrcapi", "mode": "read-only"})


@app.get("/readyz")
def ready():
    import os
    if not os.environ.get("API_AUTH"):
        return jsonify({"status": "not_ready", "reason": "auth missing"}), 503
    return jsonify({"status": "ready"})
