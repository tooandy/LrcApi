"""Entry point for native home LrcAPI, managed by macOS launchd."""
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path

from waitress import serve

from mod.args import args
from api import app, v1_bp
from api import __import__ as registered_routes


def run_server():
    if not os.environ.get("API_AUTH"):
        raise RuntimeError("API_AUTH must be configured: refusing to expose unauthenticated lyrics")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log_dir = Path(os.environ.get("LRCAPI_LOG_DIR", str(Path.home() / "Library/Logs/HomeServer/LrcAPI")))
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(str(log_dir / "log.txt"), maxBytes=2_000_000, backupCount=3)
    logging.getLogger().addHandler(handler)
    app.register_blueprint(v1_bp)
    host = args("server", "ip")
    if host in ("*", None):
        host = "127.0.0.1"
    port = int(args("server", "port") or 28883)
    logging.info("Starting read-only LrcAPI on %s:%s", host, port)
    serve(app, host=host, port=port, threads=8,
          connection_limit=100, channel_timeout=20, max_request_body_size=1024 * 1024)


if __name__ == "__main__":
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12+ required")
    run_server()
