from pathlib import Path

from core import telegram_policy


def test_infer_category_prefers_leads_and_bookings():
    assert telegram_policy.infer_category("NEW LEAD — QUOTE READY") == "lead"
    assert telegram_policy.infer_category("OVERDUE: 6 days since last booking.") == "booking"
    assert telegram_policy.infer_category("VENDOR REPLIED — CALL WITHIN 5 MINUTES") == "other"


def test_non_lead_notifications_are_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_policy, "DB_PATH", Path(tmp_path) / "guard.db")

    decision = telegram_policy.should_send_notification(
        "OUTBOUND MONITOR ALERT\nSMTP anomaly detected",
        source="test",
        recipient="chat-1",
    )

    assert decision.allowed is False
    assert decision.category == "other"
    assert decision.reason == "blocked_category:other"


def test_duplicate_lead_notifications_are_suppressed(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram_policy, "DB_PATH", Path(tmp_path) / "guard.db")

    message = "NEW LEAD — QUOTE READY\nLead #123\nQuote #456"
    first = telegram_policy.should_send_notification(
        message,
        source="test",
        recipient="chat-1",
    )
    second = telegram_policy.should_send_notification(
        message,
        source="test",
        recipient="chat-1",
    )

    assert first.allowed is True
    assert first.category == "lead"
    assert second.allowed is False
    assert second.reason.startswith("duplicate_within_")
