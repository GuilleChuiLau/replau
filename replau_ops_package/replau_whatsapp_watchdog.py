#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
GATEWAY_HEALTH_URL = os.environ.get("OPENCLAW_GATEWAY_HEALTH_URL", "http://127.0.0.1:18789/health")
OPENCLAW_CLI = os.environ.get("OPENCLAW_HEALTH_CLI", "/home/guill/.npm-global/bin/openclaw")
GATEWAY_SERVICE = os.environ.get("OPENCLAW_GATEWAY_SERVICE", "openclaw-gateway.service")
JOURNAL_SINCE = os.environ.get("WHATSAPP_WATCHDOG_JOURNAL_SINCE", "12 hours ago")
STALE_SECONDS = int(os.environ.get("WHATSAPP_WATCHDOG_STALE_SECONDS", "180"))
HEALTH_FAILURE_THRESHOLD = int(os.environ.get("WHATSAPP_WATCHDOG_HEALTH_FAILURE_THRESHOLD", "3"))
WHATSAPP_STALE_THRESHOLD = int(os.environ.get("WHATSAPP_WATCHDOG_STALE_FAILURE_THRESHOLD", "2"))
DISCONNECT_BURST_WINDOW_SECONDS = int(os.environ.get("WHATSAPP_WATCHDOG_DISCONNECT_BURST_WINDOW_SECONDS", "3600"))
DISCONNECT_BURST_THRESHOLD = int(os.environ.get("WHATSAPP_WATCHDOG_DISCONNECT_BURST_THRESHOLD", "3"))
DISCONNECT_DAILY_WINDOW_SECONDS = int(os.environ.get("WHATSAPP_WATCHDOG_DISCONNECT_DAILY_WINDOW_SECONDS", "86400"))
DISCONNECT_DAILY_THRESHOLD = int(os.environ.get("WHATSAPP_WATCHDOG_DISCONNECT_DAILY_THRESHOLD", "8"))
OUTBOX_STALE_SECONDS = int(os.environ.get("WHATSAPP_WATCHDOG_OUTBOX_STALE_SECONDS", "300"))
OUTBOX_IMPACT_THRESHOLD = int(os.environ.get("WHATSAPP_WATCHDOG_OUTBOX_IMPACT_THRESHOLD", "1"))
STATE_PATH = Path(
    os.environ.get(
        "WHATSAPP_WATCHDOG_STATE",
        "/home/guill/.local/state/replau/whatsapp_watchdog_state.json",
    )
)

CONNECTED_RE = re.compile(r"\[whatsapp\].*Listening for (?:personal )?WhatsApp inbound messages")
DISCONNECT_RE = re.compile(
    r"\[whatsapp\].*(Web connection closed|watchdog timeout|recovering a stale connection).*"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat().replace("+00:00", "Z")


def run(args: list[str], timeout: int = 12) -> dict:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=str(STATE_PATH.parent), delete=False) as tmp:
        json.dump(state, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(STATE_PATH)


def gateway_health() -> dict:
    try:
        with urlopen(GATEWAY_HEALTH_URL, timeout=8) as response:
            body = response.read(2048).decode("utf-8", "replace")
            result = {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "body": body[:500],
                "source": "http",
            }
            try:
                result["json"] = json.loads(body)
            except Exception:
                pass
            if result["ok"] and not (result.get("json") or {}).get("channels"):
                cli = run([OPENCLAW_CLI, "health", "--json"], timeout=20)
                if cli["ok"]:
                    try:
                        result["json"] = json.loads(cli["stdout"])
                        result["source"] = "openclaw health --json"
                    except Exception as exc:
                        result["cli_parse_error"] = f"{type(exc).__name__}: {exc}"
                else:
                    result["cli_error"] = cli["stderr"] or cli["stdout"]
            return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def postgrest_json(path: str) -> list[dict] | dict:
    url = POSTGREST_BASE_URL + (path if path.startswith("/") else "/" + path)
    with urlopen(url, timeout=8) as response:
        return json.loads(response.read(200000).decode("utf-8", "replace"))


def journal_events() -> list[dict]:
    result = run(
        ["journalctl", "--user", "-u", GATEWAY_SERVICE, "--since", JOURNAL_SINCE, "--no-pager", "-o", "short-iso"],
        timeout=12,
    )
    if not result["ok"]:
        return [{"kind": "journal_error", "at": iso(), "message": result["stderr"] or result["stdout"]}]

    events: list[dict] = []
    for line in result["stdout"].splitlines():
        match = re.match(r"^(\S+)\s+\S+\s+\S+\[\d+\]:\s+(.*)$", line)
        at = match.group(1) if match else ""
        message = match.group(2) if match else line
        if CONNECTED_RE.search(message):
            events.append({"kind": "connected", "at": at, "message": message})
        elif DISCONNECT_RE.search(message):
            events.append({"kind": "disconnected", "at": at, "message": message})
    return events


def seconds_since(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int((utc_now() - parsed.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def parse_event_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def disconnect_incidents(events: list[dict]) -> list[dict]:
    """Collapse multiple log lines from one outage into a single incident."""
    incidents: list[dict] = []
    current: dict | None = None
    for event in events:
        if event.get("kind") == "disconnected":
            if current is None:
                current = {
                    "disconnected_at": event.get("at"),
                    "disconnect_message": event.get("message"),
                    "log_event_count": 1,
                }
            else:
                current["log_event_count"] = int(current.get("log_event_count") or 0) + 1
        elif event.get("kind") == "connected" and current is not None:
            current["reconnected_at"] = event.get("at")
            start = parse_event_time(current.get("disconnected_at"))
            end = parse_event_time(event.get("at"))
            current["duration_seconds"] = max(0, int((end - start).total_seconds())) if start and end else None
            incidents.append(current)
            current = None
    if current is not None:
        current["reconnected_at"] = None
        current["duration_seconds"] = seconds_since(current.get("disconnected_at"))
        incidents.append(current)
    return incidents


def count_recent_disconnects(incidents: list[dict], window_seconds: int) -> int:
    cutoff = utc_now().timestamp() - window_seconds
    count = 0
    for incident in incidents:
        parsed = parse_event_time(incident.get("disconnected_at"))
        if parsed and parsed.timestamp() >= cutoff:
            count += 1
    return count


def seconds_since_epoch_ms(value: int | float | None) -> int | None:
    if value is None:
        return None
    try:
        return int((utc_now().timestamp() * 1000 - float(value)) / 1000)
    except Exception:
        return None


def outbox_impact() -> dict:
    try:
        rows = postgrest_json(
            "/whatsapp_outbox"
            "?select=id,status,created_at,last_attempt_at,error_message"
            "&status=in.(PENDING,SENDING,ERROR)"
            "&order=created_at.asc"
            "&limit=200"
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "impacting": False,
            "pending_count": None,
            "sending_count": None,
            "error_count": None,
            "stale_count": None,
        }

    if not isinstance(rows, list):
        rows = []

    counts = {"PENDING": 0, "SENDING": 0, "ERROR": 0}
    oldest_active_age = None
    stale_rows: list[dict] = []
    for row in rows:
        status = str(row.get("status") or "").upper()
        if status in counts:
            counts[status] += 1
        if status not in {"PENDING", "SENDING"}:
            continue
        created_age = seconds_since(row.get("created_at"))
        attempt_age = seconds_since(row.get("last_attempt_at"))
        age = attempt_age if status == "SENDING" and attempt_age is not None else created_age
        if age is not None:
            oldest_active_age = age if oldest_active_age is None else max(oldest_active_age, age)
            if age >= OUTBOX_STALE_SECONDS:
                stale_rows.append({"id": row.get("id"), "status": status, "age_seconds": age})

    stale_count = len(stale_rows)
    impacting = stale_count >= OUTBOX_IMPACT_THRESHOLD or counts["ERROR"] >= OUTBOX_IMPACT_THRESHOLD
    return {
        "ok": True,
        "impacting": impacting,
        "pending_count": counts["PENDING"],
        "sending_count": counts["SENDING"],
        "error_count": counts["ERROR"],
        "stale_count": stale_count,
        "oldest_active_age_seconds": oldest_active_age,
        "stale_seconds": OUTBOX_STALE_SECONDS,
        "impact_threshold": OUTBOX_IMPACT_THRESHOLD,
        "sample_stale_rows": stale_rows[:5],
    }


def increment_counter(state: dict, key: str, failed: bool) -> int:
    if not failed:
        state[key] = 0
        return 0
    value = int(state.get(key) or 0) + 1
    state[key] = value
    return value


def maybe_restart(reason: str, dry_run: bool) -> dict:
    if dry_run:
        return {"attempted": False, "dry_run": True, "reason": reason}
    result = run(["systemctl", "--user", "restart", GATEWAY_SERVICE], timeout=30)
    return {
        "attempted": True,
        "ok": result["ok"],
        "reason": reason,
        "returncode": result["returncode"],
        "stderr": result["stderr"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-restart", action="store_true")
    args = parser.parse_args()

    state = read_state()
    service = run(["systemctl", "--user", "is-active", GATEWAY_SERVICE], timeout=8)
    health = gateway_health()
    outbox = outbox_impact()
    events = journal_events()
    incidents = disconnect_incidents(events)
    whatsapp_health = (health.get("json") or {}).get("channels", {}).get("whatsapp", {})
    health_connected = bool(whatsapp_health.get("connected") or whatsapp_health.get("linked"))
    health_state = whatsapp_health.get("healthState") or whatsapp_health.get("statusState")
    health_activity_age = seconds_since_epoch_ms(
        whatsapp_health.get("lastTransportActivityAt")
        or whatsapp_health.get("lastEventAt")
        or whatsapp_health.get("lastConnectedAt")
    )

    for event in events:
        if event["kind"] == "connected":
            state["last_connected_at"] = event["at"]
            state["last_connected_message"] = event["message"]
        elif event["kind"] == "disconnected":
            state["last_disconnect_at"] = event["at"]
            state["last_disconnect_message"] = event["message"]

    last_connected = state.get("last_connected_at")
    last_disconnect = state.get("last_disconnect_at")
    disconnected_age = seconds_since(last_disconnect)
    connected_after_disconnect = bool(last_connected and last_disconnect and last_connected >= last_disconnect)
    connected = health_connected or (bool(last_connected) and (not last_disconnect or connected_after_disconnect))
    recent_disconnect_count = count_recent_disconnects(incidents, DISCONNECT_BURST_WINDOW_SECONDS)
    daily_disconnect_count = count_recent_disconnects(incidents, DISCONNECT_DAILY_WINDOW_SECONDS)
    last_incident = incidents[-1] if incidents else {}
    disconnect_burst_warning = (
        recent_disconnect_count >= DISCONNECT_BURST_THRESHOLD
        or daily_disconnect_count >= DISCONNECT_DAILY_THRESHOLD
    )
    message_impacting = bool(outbox.get("impacting"))
    health_failure_count = increment_counter(state, "gateway_health_failure_count", not health["ok"])
    whatsapp_stale_failure = bool(
        (
            whatsapp_health
            and not health_connected
            and health_activity_age is not None
            and health_activity_age >= STALE_SECONDS
        )
        or (
            last_disconnect
            and not connected_after_disconnect
            and disconnected_age is not None
            and disconnected_age >= STALE_SECONDS
        )
    )
    whatsapp_stale_failure_count = increment_counter(
        state, "whatsapp_stale_failure_count", whatsapp_stale_failure
    )

    restart_reason = None
    if args.force_restart:
        restart_reason = "manual force restart requested"
    elif not service["ok"] or service["stdout"] != "active":
        restart_reason = f"{GATEWAY_SERVICE} is not active ({service['stdout'] or 'unknown'})"
    elif not health["ok"] and health_failure_count >= HEALTH_FAILURE_THRESHOLD:
        restart_reason = (
            f"gateway health failed {health_failure_count} consecutive checks: "
            f"{health.get('error') or health.get('status')}"
        )
    elif (
        whatsapp_health
        and not health_connected
        and health_activity_age is not None
        and health_activity_age >= STALE_SECONDS
        and whatsapp_stale_failure_count >= WHATSAPP_STALE_THRESHOLD
    ):
        restart_reason = (
            f"WhatsApp health is stale/unlinked for {health_activity_age}s "
            f"across {whatsapp_stale_failure_count} checks"
        )
    elif (
        last_disconnect
        and not connected_after_disconnect
        and disconnected_age is not None
        and disconnected_age >= STALE_SECONDS
        and whatsapp_stale_failure_count >= WHATSAPP_STALE_THRESHOLD
    ):
        restart_reason = (
            f"WhatsApp disconnected/stale for {disconnected_age}s "
            f"across {whatsapp_stale_failure_count} checks"
        )

    restart = None
    if restart_reason:
        restart = maybe_restart(restart_reason, args.dry_run)
        state["last_restart_reason"] = restart_reason
        state["last_restart_result"] = restart
        if not args.dry_run:
            state["last_restart_at"] = iso()

    state.update(
        {
            "checked_at": iso(),
            "gateway_service": GATEWAY_SERVICE,
            "gateway_service_active": service["stdout"] or "unknown",
            "gateway_health_url": GATEWAY_HEALTH_URL,
            "gateway_health_ok": bool(health["ok"]),
            "gateway_health": health,
            "whatsapp_health_state": health_state,
            "whatsapp_health_connected": health_connected,
            "seconds_since_health_activity": health_activity_age,
            "connected": connected,
            "status": (
                "impacted"
                if connected and message_impacting
                else "degraded"
                if connected and disconnect_burst_warning
                else ("connected" if connected else ("stale" if restart_reason else "unknown"))
            ),
            "stale_seconds": STALE_SECONDS,
            "gateway_health_failure_threshold": HEALTH_FAILURE_THRESHOLD,
            "whatsapp_stale_failure_threshold": WHATSAPP_STALE_THRESHOLD,
            "seconds_since_disconnect": disconnected_age,
            "disconnect_burst_warning": disconnect_burst_warning,
            "disconnect_burst_window_seconds": DISCONNECT_BURST_WINDOW_SECONDS,
            "disconnect_burst_threshold": DISCONNECT_BURST_THRESHOLD,
            "disconnects_in_burst_window": recent_disconnect_count,
            "disconnect_daily_window_seconds": DISCONNECT_DAILY_WINDOW_SECONDS,
            "disconnect_daily_threshold": DISCONNECT_DAILY_THRESHOLD,
            "disconnects_in_daily_window": daily_disconnect_count,
            "last_recovery_duration_seconds": last_incident.get("duration_seconds"),
            "last_reconnected_at": last_incident.get("reconnected_at"),
            "recent_disconnect_incidents": incidents[-10:],
            "recent_event_count": len([e for e in events if e.get("kind") in {"connected", "disconnected"}]),
            "postgrest_base_url": POSTGREST_BASE_URL,
            "outbox": outbox,
            "message_impacting": message_impacting,
        }
    )
    write_state(state)

    print(json.dumps(state, indent=2, sort_keys=True))
    if restart and restart.get("attempted") and not restart.get("ok", True):
        return 2
    if restart_reason and args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
