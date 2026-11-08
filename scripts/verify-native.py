#!/usr/bin/env python3
"""Check local native LrcAPI without printing API credentials or editing music."""
import json
import os
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

root = os.environ.get("LRCAPI_TEST_BASE") or ("http://192.168.3.7:" + os.environ.get("LRCAPI_TEST_PORT", "28883"))
line = (Path.home() / ".config/home-server/lrcapi.env").read_text().strip()
assert line.startswith("API_AUTH=")
key = shlex.split(line.split("=", 1)[1])[0]
assert key

def probe(path, auth=True, timeout=20):
    req = urllib.request.Request(root + path, headers={"Authorization": key} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()

def report(name, code, body):
    print(f"{name}: http={code} bytes={len(body)}")
    return code

assert report("health", *probe("/time")) == 200
assert report("auth_denied", *probe("/lyrics?title=security-check", auth=False)) in (401,403)
assert report("legacy_lyrics", *probe("/lyrics?" + urllib.parse.urlencode({"title": "遇见", "artist": "孙燕姿"}), timeout=40)) == 200
code, body = probe("/jsonapi?" + urllib.parse.urlencode({"title": "遇见", "artist": "孙燕姿"}), timeout=40)
assert report("legacy_json", code, body) == 200
data=json.loads(body)
print("legacy_json_items=",len(data) if isinstance(data,list) else "wrong_type")
code, body = probe("/api/search?" + urllib.parse.urlencode({"track_name": "I Want to Live", "artist_name":"Borislav Slavov"}), timeout=24)
report("lrclib_search",code,body)
if code==200:
    records=json.loads(body)
    assert isinstance(records,list)
    print("lrclib_results=",len(records))
else:
    print("lrclib_search_upstream_unavailable_or_rate_limited=",code)
