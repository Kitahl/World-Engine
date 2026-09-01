from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from world_engine_autostart import register_current_install
from world_engine_connection_guard import atomic_json, install_environment, migrate_legacy_data, persistent_data_dir
from world_engine_permanent_endpoint import (
    api_key_fingerprint,
    install_cloudflare_permanent,
    install_ngrok_user_permanent,
    install_tailscale_permanent,
)


def load_json(path: Path) -> dict:
    try:
        v=json.loads(path.read_text(encoding="utf-8")); return v if isinstance(v,dict) else {}
    except Exception: return {}


def ensure_launcher_config(data: Path) -> tuple[Path,str,bool]:
    p=data/"launcher_config.json"; cfg=load_json(p); key=str(cfg.get("api_key") or "").strip(); created=False
    if len(key)<24:
        key=secrets.token_urlsafe(32); cfg["api_key"]=key; cfg["created_by"]="World Engine 5.0.0 permanent endpoint installer"; atomic_json(p,cfg); created=True
    return p,key,created


def local_health() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=1.5) as r: return r.status==200
    except Exception: return False


def python_for_root(root: Path) -> str:
    for p in (root/".venv"/"Scripts"/"python.exe", root/"venv"/"Scripts"/"python.exe"):
        if p.exists(): return str(p)
    return sys.executable


def start_backend(root: Path,data: Path,key: str):
    if local_health(): return None
    env=os.environ.copy(); env.update(WORLD_ENGINE_DATA_DIR=str(data),WORLD_ENGINE_DB=str(data/"world_engine.sqlite3"),WORLD_ENGINE_API_KEY=key,WORLD_ENGINE_HOST="127.0.0.1",PORT="8000")
    kw={"cwd":str(root),"env":env,"stdin":subprocess.DEVNULL,"stdout":subprocess.DEVNULL,"stderr":subprocess.DEVNULL}
    if os.name=="nt": kw["creationflags"]=getattr(subprocess,"DETACHED_PROCESS",0)|getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)|getattr(subprocess,"CREATE_NO_WINDOW",0)
    proc=subprocess.Popen([python_for_root(root),str(root/"app.py")],**kw)
    for _ in range(60):
        if local_health(): return proc
        if proc.poll() is not None: raise RuntimeError(f"World Engine backend exited with code {proc.returncode}")
        time.sleep(.5)
    raise RuntimeError("World Engine backend did not become healthy")


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=".")
    ap.add_argument("--provider",choices=["ngrok","tailscale-user","tailscale-admin","cloudflare"],default="ngrok")
    ap.add_argument("--url",default="")
    args=ap.parse_args()
    root=Path(args.root).resolve()
    if not (root/"app.py").exists(): raise SystemExit("Run from the World Engine folder")
    data=persistent_data_dir(); data.mkdir(parents=True,exist_ok=True)
    migration=migrate_legacy_data(root/"data",data); install_environment(data)
    _,key,created=ensure_launcher_config(data)
    register_current_install(root,python_exe=python_for_root(root),data=data)
    start_backend(root,data,key)
    print(f"[5.0.0] Local World Engine PASS; API-key fingerprint {api_key_fingerprint(key)}")
    if args.provider=="ngrok":
        print("\nNO-ADMIN PERMANENT MODE\nCreate/sign in to a free ngrok account if needed: https://dashboard.ngrok.com/signup")
        print("Copy your authtoken from: https://dashboard.ngrok.com/get-started/your-authtoken")
        try:
            import webbrowser; webbrowser.open("https://dashboard.ngrok.com/get-started/your-authtoken")
        except Exception: pass
        token=getpass.getpass("Paste ngrok authtoken (hidden): ").strip()
        result=install_ngrok_user_permanent(root,token,allow_download=True)
    elif args.provider.startswith("tailscale"):
        unattended=args.provider=="tailscale-admin"
        result=install_tailscale_permanent(root,allow_install=False,interactive=True,unattended=unattended)
    else:
        if not args.url: raise SystemExit("Cloudflare mode requires --url https://worldengine.example.com")
        token=getpass.getpass("Cloudflare named-tunnel token (hidden): ")
        result=install_cloudflare_permanent(root,args.url,token)
    marker=data/"PERMANENT_ENDPOINT_READY.txt"
    marker.write_text(
        "WORLD ENGINE PERMANENT ENDPOINT READY\n\n"
        f"Provider: {result['provider']}\nPermanent URL: {result['public_url']}\nAPI-key fingerprint: {api_key_fingerprint(key)}\nSchema: {result['schema']}\n"
        f"New API key created: {'YES' if created else 'NO'}\n\n"
        "ONE-TIME GPT SETUP: import openapi_actions_PERMANENT.json and set Bearer auth to the World Engine API key.\n",
        encoding="utf-8")
    print(json.dumps(result,indent=2))
    print(f"\n[5.0.0] PERMANENT ENDPOINT PASS: {result['public_url']}")
    print("[5.0.0] No Administrator rights were used by ngrok user mode.")
    return 0

if __name__=="__main__": raise SystemExit(main())
