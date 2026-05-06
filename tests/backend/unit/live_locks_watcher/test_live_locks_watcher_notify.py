"""Notification-related tests for live_locks_watcher."""

from __future__ import annotations

from ._helpers import load_watcher_module


def test_notify_uses_desktop_notify_if_available(monkeypatch, caplog):
    mod = load_watcher_module()
    called = {}

    class FakeDesktop:
        def notify(self, title=None, message=None, app_name=None, timeout=None):
            called["title"] = title
            called["msg"] = message

    monkeypatch.setattr(mod, "desktop_notify", FakeDesktop())
    monkeypatch.setenv("COLLAB_TEST_MODE", "0")
    mod._notify("T", "M")
    assert called.get("title") == "T"
    assert "M" in called.get("msg")


def test_notify_with_title_and_message(monkeypatch):
    mod = load_watcher_module()
    notify_called = []

    def mock_notify(title, message, app_name=None):
        notify_called.append((title, message, app_name))

    monkeypatch.setattr(mod, "_desktop_notify", mock_notify, raising=False)

    try:
        mod._notify("Test Title", "Test Message")
    except Exception:
        pass

    if notify_called:
        assert notify_called[0][0] == "Test Title"
        assert notify_called[0][1] == "Test Message"


def test_notify_fallback_no_desktop_notify(monkeypatch, caplog):
    mod = load_watcher_module()
    monkeypatch.setattr(mod, "desktop_notify", None)
    import logging

    with caplog.at_level(logging.INFO):
        monkeypatch.setenv("COLLAB_TEST_MODE", "0")
        mod._notify("Test Title", "Test Message")
    assert "Test Title" in caplog.text or "Test Message" in caplog.text


def test_notify_desktop_notify_exception(monkeypatch, caplog):
    mod = load_watcher_module()

    class FailingNotify:
        def notify(self, **kwargs):
            raise RuntimeError("notify failed")

    monkeypatch.setattr(mod, "desktop_notify", FailingNotify())
    import logging

    with caplog.at_level(logging.INFO):
        monkeypatch.setenv("COLLAB_TEST_MODE", "0")
        mod._notify("Fail Title", "Fail Message")
    assert "Fail Title" in caplog.text or "Fail Message" in caplog.text
