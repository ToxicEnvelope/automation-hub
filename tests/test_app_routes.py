from __future__ import annotations

from src.app import app


def test_failure_chat_route_is_registered() -> None:
    routes = {
        (route.path, tuple(sorted(getattr(route, "methods", None) or [])))
        for route in app.routes
    }
    assert any(path == "/api/ai/failure-chat" and "POST" in methods for path, methods in routes)
