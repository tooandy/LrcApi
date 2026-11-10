"""LRCLIB v1 compatible read routes with the household's existing API token.

Native LRCLIB clients that cannot set Authorization remain unsupported, by design.
"""
from flask import Response, jsonify, request

from . import app
from mod.auth import require_auth_decorator
from mod.searchx.lrclib import CLIENT

ALLOWED = {"track_name", "artist_name", "album_name", "duration", "q"}


def _response(status, data, headers=None):
    response = Response(data, status=status, mimetype="application/json")
    for key, value in (headers or {}).items():
        if key == "Retry-After":
            response.headers[key] = value
    return response


def _params(search=False):
    values = request.args.to_dict(flat=True)
    if len(values) > 5 or any(k not in ALLOWED for k in values):
        return None
    if any(len(value) > 256 for value in values.values()):
        return None
    if not search:
        if not values.get("track_name") or not values.get("artist_name"):
            return None
        if "q" in values:
            return None
        if "duration" in values:
            try:
                length = float(values["duration"])
            except (ValueError, OverflowError):
                return None
            if not 1 <= length <= 3600:
                return None
    return values


@app.get("/api/get")
@app.get("/api/get-cached")
@require_auth_decorator("r")
def lrclib_get():
    values = _params()
    if values is None:
        return jsonify({"message": "Invalid LRCLIB track signature", "statusCode": 400}), 400
    cached = request.path.endswith("/get-cached")
    return _response(*CLIENT.get("/api/get", values, only_cached=cached))


@app.get("/api/search")
@require_auth_decorator("r")
def lrclib_search():
    values = _params(search=True)
    if values is None:
        return jsonify({"message": "Invalid LRCLIB search query", "statusCode": 400}), 400
    if not values.get("q") and not values.get("track_name"):
        return jsonify([])
    return _response(*CLIENT.get("/api/search", values))


@app.get("/api/v2/search")
@require_auth_decorator("r")
def lrclib_search_v2():
    values = _params(search=True)
    if values is None:
        return jsonify({"message": "Invalid query", "statusCode": 400}), 400
    if not values.get("q") and not values.get("track_name"):
        return jsonify([])
    return _response(*CLIENT.get("/api/v2/search", values))


@app.get("/api/get/<int:track_id>")
@require_auth_decorator("r")
def lrclib_get_id(track_id):
    if track_id < 1 or track_id > 10**13 or request.args:
        return jsonify({"message": "Invalid LRCLIB ID", "statusCode": 400}), 400
    return _response(*CLIENT.get(f"/api/get/{track_id}"))
