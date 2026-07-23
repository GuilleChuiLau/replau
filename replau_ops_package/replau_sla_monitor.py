#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import requests

BASE=os.environ.get("POSTGREST_BASE_URL","http://127.0.0.1:3000").rstrip("/")
STATUS_PATH=Path(os.environ.get("REPLAU_RESTAURANT_STATUS_PATH","/home/guill/.openclaw/workspace/replau_restaurant_status.json"))
WARNING=int(os.environ.get("WHATSAPP_SLA_WARNING_MINUTES","10"))
URGENT=int(os.environ.get("WHATSAPP_SLA_URGENT_MINUTES","15"))
COOLDOWN=int(os.environ.get("WHATSAPP_SLA_COOLDOWN_MINUTES","30"))
DESKTOP=os.environ.get("SLA_DESKTOP_NOTIFICATIONS","true").lower()=="true"

def restaurant_quiet()->bool:
    try:
        data=json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return not bool(data.get("accepting_orders",True))
    except FileNotFoundError:
        return False
    except Exception:
        return True

def desktop_notice(item:dict)->None:
    if not DESKTOP: return
    level=str(item.get("level") or "WARNING")
    wait=int(item.get("wait_minutes") or 0)
    name=str(item.get("sender_name") or "WhatsApp customer")[:80]
    urgency="critical" if level=="URGENT" else "normal"
    try:
        subprocess.run(["notify-send","--urgency",urgency,"Replau WhatsApp SLA",f"{level}: {name} waiting {wait} minutes"],timeout=5,check=False,capture_output=True)
    except (FileNotFoundError,subprocess.SubprocessError):
        pass

def run()->dict:
    quiet=restaurant_quiet()
    response=requests.post(f"{BASE}/rpc/evaluate_whatsapp_sla_alerts",json={
        "p_warning_minutes":WARNING,"p_urgent_minutes":URGENT,
        "p_cooldown_minutes":COOLDOWN,"p_quiet":quiet},timeout=20)
    response.raise_for_status()
    result=response.json()
    if not isinstance(result,dict) or result.get("ok") is not True: raise RuntimeError("invalid SLA evaluation response")
    for item in result.get("notifications",[]): desktop_notice(item)
    return result

if __name__=="__main__":
    try: print(json.dumps(run(),sort_keys=True))
    except Exception as exc:
        print(f"SLA monitor failed: {exc}",file=sys.stderr); raise SystemExit(1)
