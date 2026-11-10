"""Compatibility shim: reject absent credentials and require exact permission subsets."""
import hmac
from mod.args import args


def get_permission(value):
    allowed = args("auth")
    if not value or not allowed:
        return ""
    if isinstance(allowed, dict):
        return allowed.get(value, "")
    return "r" if hmac.compare_digest(str(value), str(allowed)) else ""


def has_permission(supply, required):
    return supply == "all" or (
        bool(supply) and bool(required) and set(required).issubset(set(supply)))


def require_auth(request, permission="r"):
    expected = args("auth")
    if not expected:
        return -1
    supplied = request.headers.get("Authorization", "") or request.headers.get("Authentication", "")
    return 1 if has_permission(get_permission(supplied), permission) else -1
