import shutil
import logging
import sys
import os

from flask import Flask, Blueprint, request, g
from flask_caching import Cache

app = Flask(__name__)
logger = logging.getLogger(__name__)

v1_bp = Blueprint('v1', __name__, url_prefix='/api/v1')
v1_bp.config = app.config.copy()

navidrome_bp = Blueprint('navidrome', __name__, url_prefix='/api/navidrome')
navidrome_bp.config = app.config.copy()


# 将缓存放在代码树外，并使用稳定的 key，以支持原生进程重启后复用。
import hashlib
import json
cache_dir = os.environ.get("LRCAPI_CACHE_DIR", os.path.join(os.getcwd(), "flask_cache"))
os.makedirs(cache_dir, exist_ok=True)
cache = Cache(app, config={"CACHE_TYPE": "filesystem", "CACHE_DIR": cache_dir})


def make_cache_key(*args, **kwargs) -> str:
    data = json.dumps(
        [request.path, sorted(request.args.items(multi=True)),
         request.headers.get("Authorization", "") or request.headers.get("Authentication", ""),
         request.cookies.get("api_auth_token", "")],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@app.before_request
def home_server_readonly():
    # NAS v2 RAID 当前降级，Mac 控制面 LrcAPI 不允许任何文件修改接口。
    if os.environ.get("LRCAPI_READ_ONLY") != "1":
        return None
    path = request.path
    write_prefixes = (
        "/tag", "/confirm", "/translate", "/api/v1/tag",
        "/api/v1/translate", "/api/v1/file/", "/api/v1/db",
    )
    if request.method not in ("GET", "HEAD", "OPTIONS") or path.startswith(write_prefixes):
        from flask import jsonify
        return jsonify({"error": "read-only service"}), 403
    return None


def get_base_path():
    """
    获取主程序所在目录（基于入口脚本路径）
    """
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
        
    # 判断是否以模块方式运行
    if __package__:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    else:
        return os.path.dirname(os.path.abspath(sys.argv[0]))


src_path = os.path.join(get_base_path(), 'src')  # 静态资源路径

__all__ = ['app', 'v1_bp', 'cache', 'make_cache_key', 'logger', 'src_path']
