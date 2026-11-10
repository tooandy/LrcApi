#!/usr/bin/env python3
"""Safe Mac LaunchAgent cutover to the uv-managed feature worktree.

No Nginx config changes. Always retains an exact old plist backup.
Executes only when launched explicitly by the owner, never on import.
"""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import urllib.parse
import sys

HOME = Path.home()
LIVE_PLIST = HOME / "Library/LaunchAgents/local.home-server.lrcapi.plist"
PREVIOUS_PLIST = HOME / "Library/LaunchAgents/local.home-server.lrcapi.plist.bak-before-uv-20261111"
LEGACY_ROOT = HOME / "Documents/Code/LrcApi"
NEXT_ROOT = HOME / "Documents/Code/LrcApi-next"
JOB = f"gui/{subprocess.run(['id','-u'],capture_output=True,text=True,check=True).stdout.strip()}"
LABEL = "local.home-server.lrcapi"


def launch(*args):
    return subprocess.run(["launchctl",*args],capture_output=True,text=True,check=True)


def reload_plist():
    subprocess.run(["launchctl","bootout",f"{JOB}/{LABEL}"],capture_output=True,text=True)
    time.sleep(.3)
    launch("bootstrap",JOB,str(LIVE_PLIST))


def check_status(path, auth=None, host="192.168.3.7", port=28883, timeout=8):
    query = f"http://{host}:{port}{path}"
    headers = {"Authorization":auth} if auth else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(query,headers=headers),timeout=timeout) as result:
            return result.status,len(result.read(50000))
    except urllib.error.HTTPError as err:
        return err.code, 0
    except Exception:
        return 0, 0


def wait_healthy(require_new):
    path = "/healthz" if require_new else "/time"
    for _ in range(15):
        status,_ = check_status(path)
        if status == 200:
            return
        time.sleep(1)
    raise RuntimeError("New service failed health check in 15 attempts")


def validate(auth):
    assert check_status("/healthz")[0] == 200
    assert check_status("/readyz")[0] == 200
    assert check_status("/lyrics?title=security-check")[0] == 401
    sidecar = urllib.parse.urlencode({"path":"/music/Adele/2008--Chasing Pavements/02.That’s It, I Quit, I’m Moving On.m4a"})
    code,size=check_status("/lyrics?"+sidecar,auth)
    assert code==200 and size>300, (code,size)
    meta = urllib.parse.urlencode({"path":"/music/Adele/2008--19/03.Chasing Pavements.m4a"})
    code,size=check_status("/lyrics?"+meta,auth)
    assert code==200 and size>300, (code,size)
    assert check_status("/time",host="192.168.3.2")[0] == 200


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--rollback",action="store_true")
    options=parser.parse_args()
    current=plistlib.loads(LIVE_PLIST.read_bytes())
    if current.get("Label") != LABEL:
        raise RuntimeError("Unexpected launchd label")
    next_cmd=str(NEXT_ROOT/"scripts/start-native.sh")
    old_cmd=str(LEGACY_ROOT/"scripts/start-native.sh")
    if options.rollback:
        if not PREVIOUS_PLIST.exists():
            raise RuntimeError("No previous version backed up")
        LIVE_PLIST.write_bytes(PREVIOUS_PLIST.read_bytes())
        reload_plist()
        wait_healthy(require_new=False)
        print("Rolled back to previous native LrcAPI; HTTP /time 200")
        return 0

    if not NEXT_ROOT.joinpath(".venv/bin/python").exists() or not NEXT_ROOT.joinpath("uv.lock").exists():
        raise RuntimeError("New uv environment is not ready")
    if current["ProgramArguments"][-1] != old_cmd:
        raise RuntimeError("Production plist is not at expected old entrypoint; refusing to overwrite")
    shutil.copy2(LIVE_PLIST,PREVIOUS_PLIST)
    updated=dict(current)
    updated["ProgramArguments"]=[*current["ProgramArguments"][:-1],next_cmd]
    LIVE_PLIST.write_bytes(plistlib.dumps(updated))
    try:
        reload_plist()
        wait_healthy(require_new=True)
        import shlex
        line=(HOME/".config/home-server/lrcapi.env").read_text().strip()
        assert line.startswith("API_AUTH=")
        auth=shlex.split(line.split("=",1)[1])[0]
        validate(auth)
    except Exception:
        print("Cutover verification failed; restoring prior plist",file=sys.stderr)
        LIVE_PLIST.write_bytes(PREVIOUS_PLIST.read_bytes())
        reload_plist()
        wait_healthy(require_new=False)
        raise
    print("Production upgraded; read-only LrcAPI + NFS .lrc + embedded + Debian HTTP checked")
    print("Rollback backup",PREVIOUS_PLIST)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
