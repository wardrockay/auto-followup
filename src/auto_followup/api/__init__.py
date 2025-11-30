"""
API Layer.

Flask HTTP endpoints and error handling.
"""

# Import api_bp at usage time to avoid circular imports
# The blueprint is registered in app.py via:
#   from auto_followup.api.routes import api_bp

__all__ = ["api_bp"]


def __getattr__(name: str):
    """Lazy import to avoid circular dependencies."""
    if name == "api_bp":
        from auto_followup.api.routes import api_bp
        return api_bp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
