#!/usr/bin/env python3
from __future__ import annotations
import os
from datetime import datetime, timezone
import requests
BASE=os.environ.get("POSTGREST_BASE_URL","http://127.0.0.1:3000").rstrip("/")
TIMEOUT=int(os.environ.get("REQUEST_TIMEOUT","8"))
OUTBOX_MAX=int(os.environ.get("OUTBOX_MAX_ATTEMPTS","5"))
EMAIL_MAX=int(os.environ.get("EMAIL_MAX_ATTEMPTS","5"))
def get(path):
    r=requests.get(BASE+path,timeout=TIMEOUT); r.raise_for_status(); return r.json()
def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Replau stuck monitor")
    failures=[]
    pending=get("/v_whatsapp_outbox?status=eq.PENDING&select=id,pedido_num,event_type,status,attempts,last_attempt_at,created_at,error_message&order=id.desc&limit=100")
    stuck=[r for r in pending if int(r.get("attempts") or 0)>=OUTBOX_MAX]
    errors=get("/v_whatsapp_outbox?status=eq.ERROR&select=id,pedido_num,event_type,status,attempts,last_attempt_at,created_at,error_message&order=id.desc&limit=100")
    emails=get("/email_logistica_log?status=eq.PENDING&select=id,pedido_id,recipient,status,created_at,error_message&order=id.desc&limit=100")
    email_stuck=[r for r in emails if int(r.get("attempts") or 0)>=EMAIL_MAX]
    if stuck: failures.append(f"stuck WhatsApp rows={len(stuck)}"); print("STUCK WHATSAPP:", stuck)
    if errors: failures.append(f"ERROR WhatsApp rows={len(errors)}"); print("ERROR WHATSAPP:", errors)
    if email_stuck: failures.append(f"stuck email rows={len(email_stuck)}"); print("STUCK EMAIL:", email_stuck)
    if failures: print("CRITICAL:", " | ".join(failures)); return 1
    print("OK: no stuck rows"); return 0
if __name__=="__main__":
    raise SystemExit(main())
