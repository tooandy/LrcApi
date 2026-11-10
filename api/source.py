"""Minimal non-interactive landing page. No file viewer or web login."""
from flask import jsonify
from . import app


@app.get("/")
@app.get("/src")
def homepage():
    return jsonify({"name": "Home LrcAPI", "mode": "read-only",
                    "endpoints": ["/lyrics", "/jsonapi", "/api/get", "/api/search", "/healthz"]})
