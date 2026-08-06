from __future__ import annotations

from src.app import app


def test_failure_chat_route_is_registered() -> None:
    routes = {
        (route.path, tuple(sorted(getattr(route, "methods", None) or [])))
        for route in app.routes
    }
    assert any(path == "/api/ai/failure-chat" and "POST" in methods for path, methods in routes)


def test_dashboard_html_uses_content_hashed_static_assets() -> None:
    from src.app import home

    response = home()
    body = response.body.decode("utf-8")

    assert "__STATIC_ASSET_VERSION__" not in body
    assert "/static/styles.css?v=" in body
    assert "/static/app.js?v=" in body
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_chat_panel_starts_inert_and_accessibly_hidden() -> None:
    from src.app import home

    body = home().body.decode("utf-8")
    assert 'id="aiChatPanel"' in body
    assert 'role="dialog"' in body
    assert 'aria-labelledby="aiChatTitle"' in body
    assert 'aria-hidden="true" inert' in body
