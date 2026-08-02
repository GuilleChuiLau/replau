from datetime import datetime, timezone
from unittest.mock import Mock

import requests

import replau_conversation_retention as retention
import replau_stuck_monitor as monitor


def test_paused_policy_acknowledges_only_failures_before_pause():
    policy = {"state": "PAUSED", "updated_at": "2026-07-26T21:56:48-05:00"}
    rows = [
        {"id": 1, "last_attempt_at": "2026-07-24T11:44:33-05:00"},
        {"id": 2, "last_attempt_at": "2026-07-27T11:44:33-05:00"},
    ]
    actionable, historical = monitor.split_actionable_whatsapp(rows, policy)
    assert [row["id"] for row in actionable] == [2]
    assert [row["id"] for row in historical] == [1]


def test_active_policy_uses_activation_as_new_incident_baseline():
    rows = [
        {"id": 1, "last_attempt_at": "2026-07-24T11:44:33-05:00"},
        {"id": 2, "last_attempt_at": "2026-08-02T11:44:33-05:00"},
    ]
    actionable, historical = monitor.split_actionable_whatsapp(
        rows, {"state": "ACTIVE", "updated_at": "2026-08-01T16:26:13-05:00"}
    )
    assert [row["id"] for row in actionable] == [2]
    assert [row["id"] for row in historical] == [1]


def test_email_pending_is_ignored_when_channel_disabled():
    rows = [{"id": 1, "created_at": "2026-07-14T11:27:08-05:00"}]
    assert monitor.stale_email_rows(rows, False, 30) == []


def test_email_pending_uses_age_when_channel_enabled():
    rows = [
        {"id": 1, "created_at": "2026-07-14T11:00:00+00:00"},
        {"id": 2, "created_at": "2026-07-14T11:50:00+00:00"},
    ]
    stale = monitor.stale_email_rows(
        rows, True, 30, datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    )
    assert [row["id"] for row in stale] == [1]


def test_retention_retries_startup_503(monkeypatch):
    unavailable = Mock(status_code=503)
    unavailable.raise_for_status.side_effect = requests.HTTPError("unavailable")
    healthy = Mock(status_code=200)
    healthy.raise_for_status.return_value = None
    monkeypatch.setattr(retention.requests, "post", Mock(side_effect=[unavailable, healthy]))
    monkeypatch.setattr(retention.time, "sleep", Mock())
    monkeypatch.setenv("RETENTION_HTTP_ATTEMPTS", "2")

    assert retention.post_with_startup_retry("http://example.test/rpc", {}) is healthy
    retention.time.sleep.assert_called_once()


def test_retention_does_not_retry_client_error(monkeypatch):
    rejected = Mock(status_code=400)
    rejected.raise_for_status.side_effect = requests.HTTPError("bad request")
    post = Mock(return_value=rejected)
    monkeypatch.setattr(retention.requests, "post", post)
    monkeypatch.setattr(retention.time, "sleep", Mock())

    try:
        retention.post_with_startup_retry("http://example.test/rpc", {})
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("expected client error")
    post.assert_called_once()
    retention.time.sleep.assert_not_called()
