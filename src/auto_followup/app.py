"""
Flask Application Factory.

Creates and configures the Flask application for Cloud Run.
"""

import os
import signal
import sys
from typing import Optional

from flask import Flask

from auto_followup.api import api_bp
from auto_followup.infrastructure.logging import log_request_context, logger


def _handle_sigterm(signum: int, frame) -> None:
    """
    Handle SIGTERM for graceful shutdown on Cloud Run.
    
    Cloud Run sends SIGTERM before stopping the container.
    """
    logger.info(
        "Received SIGTERM, shutting down gracefully",
        extra={"extra_fields": {"signal": signum}}
    )
    sys.exit(0)


# Register SIGTERM handler for Cloud Run graceful shutdown
signal.signal(signal.SIGTERM, _handle_sigterm)


def create_app(config: Optional[dict] = None) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        config: Optional configuration dictionary.
        
    Returns:
        Configured Flask application.
    """
    app = Flask(__name__)
    
    app.config["JSON_SORT_KEYS"] = False
    
    if config:
        app.config.update(config)
    
    log_request_context(app)
    
    app.register_blueprint(api_bp)
    
    logger.info(
        "Application initialized",
        extra={"extra_fields": {
            "environment": os.environ.get("ENVIRONMENT", "development"),
        }}
    )
    
    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("ENVIRONMENT", "development") == "development"
    
    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug,
    )
