"""Header-only token authentication. No cookies, login pages or write roles."""
from functools import wraps
import hmac
import os
from flask import jsonify, request


def require_auth_decorator(permission="r"):
    def decorator(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            # The deployed instance supports only the read capability.
            if permission != "r":
                return jsonify({"error": "permission denied"}), 403
            expected = os.environ.get("API_AUTH", "")
            supplied = request.headers.get("Authorization", "") or request.headers.get("Authentication", "")
            if not expected or not supplied or not hmac.compare_digest(supplied, expected):
                return jsonify({"error": "unauthorized"}), 401
            return handler(*args, **kwargs)
        return wrapped
    return decorator
