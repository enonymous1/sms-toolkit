"""Flask viewer for the SMS backup database."""

from __future__ import annotations

from typing import Any

__all__ = ["create_app", "run_viewer"]


def __getattr__(name: str) -> Any:
    """Lazily re-export the Flask app factory.

    An eager ``from sms_toolkit.viewer.app import ...`` here made *any*
    import of this package (including Flask-free modules like
    ``queries``) require Flask — which broke scripts/freeze.py on the
    base install, caught by the Pages workflow on 2026-11-01. The
    factory is only loaded when actually requested, so
    ``from sms_toolkit.viewer import create_app`` keeps working while
    ``from sms_toolkit.viewer import queries`` stays Flask-free.

    Note for type checkers: the lazy names resolve to ``Any``, so
    typed callers in ``src/`` should import from
    ``sms_toolkit.viewer.app`` directly (see ``cli._run_serve``).
    """
    if name in __all__:
        from sms_toolkit.viewer.app import create_app, run_viewer

        return {"create_app": create_app, "run_viewer": run_viewer}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
