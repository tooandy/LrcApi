"""LrcAPI read-only HTTP application; safe-by-default, no writable media routes."""
import logging
from flask import Flask, Blueprint, request, jsonify

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
app.config["JSON_AS_ASCII"] = False
logger = logging.getLogger(__name__)
v1_bp = Blueprint("v1", __name__, url_prefix="/api/v1")


@app.before_request
def read_only_method_guard():
    # Fail closed regardless of environment configuration.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return jsonify({"error": "read-only service"}), 403


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "private, no-store"
    return response


__all__ = ["app", "v1_bp", "logger"]
