"""
Structured JSON Logger for Google Cloud Run
============================================

Module de logging structuré pour Cloud Run avec support de:
- Logs JSON sur stdout (compatibles Google Cloud Logging)
- Severity levels Google Cloud (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Champs contextuels automatiques (request_id, task_id, duration_ms, etc.)
- Corrélation des requêtes Cloud Run
"""

import json
import logging
import sys
import time
import os
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Optional, Dict
from flask import request, g


class JsonFormatter(logging.Formatter):
    """
    Formatter JSON pour Google Cloud Logging.
    Produit des logs au format JSON une ligne par événement.
    """
    
    # Mapping Python logging levels → Google Cloud severity
    SEVERITY_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }
    
    def format(self, record: logging.LogRecord) -> str:
        # Structure de base du log
        log_entry: Dict[str, Any] = {
            "severity": self.SEVERITY_MAP.get(record.levelno, "INFO"),
            "message": record.getMessage(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
        }
        
        # Ajouter les champs contextuels depuis Flask g (si disponibles)
        try:
            if hasattr(g, 'request_id') and g.request_id:
                log_entry["request_id"] = g.request_id
            if hasattr(g, 'task_id') and g.task_id:
                log_entry["task_id"] = g.task_id
            if hasattr(g, 'endpoint') and g.endpoint:
                log_entry["endpoint"] = g.endpoint
        except RuntimeError:
            # Hors contexte Flask
            pass
        
        # Ajouter les champs extra passés au log
        if hasattr(record, 'extra_fields') and record.extra_fields:
            for key, value in record.extra_fields.items():
                # Ne jamais logger de secrets
                if not self._is_sensitive(key):
                    log_entry[key] = self._sanitize_value(value)
        
        # Ajouter exception si présente
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Ajouter localisation du code
        if record.levelno >= logging.WARNING:
            log_entry["source"] = {
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName,
            }
        
        return json.dumps(log_entry, ensure_ascii=False, default=str)
    
    def _is_sensitive(self, key: str) -> bool:
        """Vérifie si une clé correspond à des données sensibles."""
        sensitive_patterns = [
            'password', 'secret', 'token', 'api_key', 'apikey',
            'authorization', 'auth', 'credential', 'private',
            'ssn', 'credit_card', 'card_number'
        ]
        key_lower = key.lower()
        return any(pattern in key_lower for pattern in sensitive_patterns)
    
    def _sanitize_value(self, value: Any) -> Any:
        """Sanitize les valeurs pour éviter les fuites de données."""
        if isinstance(value, str) and len(value) > 1000:
            return value[:1000] + "... [truncated]"
        return value


class StructuredLogger(logging.LoggerAdapter):
    """
    Logger adapté pour ajouter des champs structurés aux logs.
    """
    
    def process(self, msg, kwargs):
        # Extraire les extra fields
        extra = kwargs.get('extra', {})
        extra_fields = extra.pop('extra_fields', {})
        
        # Fusionner avec les champs du contexte
        if self.extra:
            extra_fields = {**self.extra, **extra_fields}
        
        kwargs['extra'] = {**extra, 'extra_fields': extra_fields}
        return msg, kwargs
    
    def with_fields(self, **fields) -> 'StructuredLogger':
        """Crée un nouveau logger avec des champs additionnels."""
        new_extra = {**self.extra, **fields}
        return StructuredLogger(self.logger, new_extra)


def get_logger(name: str = "app") -> StructuredLogger:
    """
    Crée et configure un logger structuré JSON.
    
    Usage:
        logger = get_logger("auto-followup")
        logger.info("Traitement démarré", extra={"extra_fields": {"draft_id": "abc123"}})
    """
    logger = logging.getLogger(name)
    
    # Éviter les handlers dupliqués
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        
        # Handler stdout avec format JSON
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(JsonFormatter())
        
        logger.addHandler(handler)
        
        # Désactiver la propagation pour éviter les doublons
        logger.propagate = False
    
    return StructuredLogger(logger, {})


def log_request_context(app):
    """
    Middleware Flask pour ajouter le contexte de requête aux logs.
    
    Usage:
        app = Flask(__name__)
        log_request_context(app)
    """
    @app.before_request
    def before_request():
        # Request ID (depuis Cloud Run ou généré)
        g.request_id = request.headers.get('X-Cloud-Trace-Context', '').split('/')[0] or str(uuid.uuid4())[:8]
        
        # Task ID pour Cloud Tasks
        g.task_id = request.headers.get('X-CloudTasks-TaskName')
        
        # Endpoint
        g.endpoint = request.endpoint
        
        # Timestamp de début pour calculer la durée
        g.start_time = time.time()
    
    @app.after_request
    def after_request(response):
        # Calculer la durée
        duration_ms = None
        if hasattr(g, 'start_time'):
            duration_ms = int((time.time() - g.start_time) * 1000)
        
        # Logger la requête
        logger = get_logger("request")
        logger.info(
            f"{request.method} {request.path} -> {response.status_code}",
            extra={"extra_fields": {
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }}
        )
        
        return response


def log_duration(logger: StructuredLogger, operation: str):
    """
    Décorateur pour mesurer et logger la durée d'une opération.
    
    Usage:
        @log_duration(logger, "process_followup")
        def my_function():
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration_ms = int((time.time() - start) * 1000)
                logger.info(
                    f"{operation} completed",
                    extra={"extra_fields": {
                        "operation": operation,
                        "duration_ms": duration_ms,
                        "status": "success"
                    }}
                )
                return result
            except Exception as e:
                duration_ms = int((time.time() - start) * 1000)
                logger.error(
                    f"{operation} failed: {str(e)}",
                    extra={"extra_fields": {
                        "operation": operation,
                        "duration_ms": duration_ms,
                        "status": "error",
                        "error_type": type(e).__name__
                    }}
                )
                raise
        return wrapper
    return decorator


# Logger global pour l'application
logger = get_logger("auto-followup")
