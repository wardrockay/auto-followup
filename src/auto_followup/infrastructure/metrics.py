"""
Application Metrics.

Provides Prometheus-compatible metrics for monitoring.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from flask import Flask, Response, g, request

from auto_followup.infrastructure.logging import get_logger


logger = get_logger(__name__)


@dataclass
class MetricValue:
    """A single metric value with labels."""
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Counter:
    """A monotonically increasing counter metric."""
    
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._values: Dict[str, float] = defaultdict(float)
        self._lock = Lock()
    
    def inc(self, value: float = 1.0, **labels: str) -> None:
        """Increment the counter."""
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] += value
    
    def _labels_key(self, labels: Dict[str, str]) -> str:
        """Create a unique key from labels."""
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    
    def collect(self) -> List[MetricValue]:
        """Collect all values."""
        with self._lock:
            return [
                MetricValue(value=v, labels=self._parse_labels(k))
                for k, v in self._values.items()
            ]
    
    def _parse_labels(self, key: str) -> Dict[str, str]:
        """Parse labels from key."""
        if not key:
            return {}
        labels = {}
        for part in key.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                labels[k] = v.strip('"')
        return labels


class Histogram:
    """A histogram metric for tracking distributions."""
    
    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    
    def __init__(
        self,
        name: str,
        description: str,
        buckets: tuple = DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.description = description
        self.buckets = buckets
        self._counts: Dict[str, Dict[float, int]] = defaultdict(lambda: defaultdict(int))
        self._sums: Dict[str, float] = defaultdict(float)
        self._totals: Dict[str, int] = defaultdict(int)
        self._lock = Lock()
    
    def observe(self, value: float, **labels: str) -> None:
        """Record an observation."""
        key = self._labels_key(labels)
        with self._lock:
            self._sums[key] += value
            self._totals[key] += 1
            for bucket in self.buckets:
                if value <= bucket:
                    self._counts[key][bucket] += 1
    
    def _labels_key(self, labels: Dict[str, str]) -> str:
        """Create a unique key from labels."""
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))


class Gauge:
    """A gauge metric that can go up and down."""
    
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._values: Dict[str, float] = defaultdict(float)
        self._lock = Lock()
    
    def set(self, value: float, **labels: str) -> None:
        """Set the gauge value."""
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] = value
    
    def inc(self, value: float = 1.0, **labels: str) -> None:
        """Increment the gauge."""
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] += value
    
    def dec(self, value: float = 1.0, **labels: str) -> None:
        """Decrement the gauge."""
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] -= value
    
    def _labels_key(self, labels: Dict[str, str]) -> str:
        """Create a unique key from labels."""
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))


class MetricsRegistry:
    """Registry for all application metrics."""
    
    def __init__(self) -> None:
        # HTTP metrics
        self.http_requests_total = Counter(
            "http_requests_total",
            "Total number of HTTP requests",
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds",
        )
        self.http_requests_in_progress = Gauge(
            "http_requests_in_progress",
            "Number of HTTP requests currently being processed",
        )
        
        # Business metrics
        self.followups_scheduled_total = Counter(
            "followups_scheduled_total",
            "Total number of followups scheduled",
        )
        self.followups_processed_total = Counter(
            "followups_processed_total",
            "Total number of followups processed",
        )
        self.followups_cancelled_total = Counter(
            "followups_cancelled_total",
            "Total number of followups cancelled",
        )
        self.followups_failed_total = Counter(
            "followups_failed_total",
            "Total number of followups that failed",
        )
        
        # External service metrics
        self.external_requests_total = Counter(
            "external_requests_total",
            "Total number of external service requests",
        )
        self.external_request_duration_seconds = Histogram(
            "external_request_duration_seconds",
            "External service request latency in seconds",
        )
        self.circuit_breaker_state = Gauge(
            "circuit_breaker_state",
            "Current state of circuit breakers (0=closed, 1=half-open, 2=open)",
        )
    
    def to_prometheus_format(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        
        # HTTP requests total
        lines.append(f"# HELP {self.http_requests_total.name} {self.http_requests_total.description}")
        lines.append(f"# TYPE {self.http_requests_total.name} counter")
        for mv in self.http_requests_total.collect():
            labels = ",".join(f'{k}="{v}"' for k, v in mv.labels.items())
            label_str = f"{{{labels}}}" if labels else ""
            lines.append(f"{self.http_requests_total.name}{label_str} {mv.value}")
        
        # Followups scheduled
        lines.append(f"# HELP {self.followups_scheduled_total.name} {self.followups_scheduled_total.description}")
        lines.append(f"# TYPE {self.followups_scheduled_total.name} counter")
        for mv in self.followups_scheduled_total.collect():
            labels = ",".join(f'{k}="{v}"' for k, v in mv.labels.items())
            label_str = f"{{{labels}}}" if labels else ""
            lines.append(f"{self.followups_scheduled_total.name}{label_str} {mv.value}")
        
        # Followups processed
        lines.append(f"# HELP {self.followups_processed_total.name} {self.followups_processed_total.description}")
        lines.append(f"# TYPE {self.followups_processed_total.name} counter")
        for mv in self.followups_processed_total.collect():
            labels = ",".join(f'{k}="{v}"' for k, v in mv.labels.items())
            label_str = f"{{{labels}}}" if labels else ""
            lines.append(f"{self.followups_processed_total.name}{label_str} {mv.value}")
        
        return "\n".join(lines) + "\n"


# Global metrics registry
_metrics: Optional[MetricsRegistry] = None


def get_metrics() -> MetricsRegistry:
    """Get global metrics registry."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsRegistry()
    return _metrics


def setup_metrics_middleware(app: Flask) -> None:
    """
    Setup Flask middleware for automatic HTTP metrics collection.
    
    Args:
        app: Flask application instance.
    """
    metrics = get_metrics()
    
    @app.before_request
    def before_request() -> None:
        g.metrics_start_time = time.time()
        metrics.http_requests_in_progress.inc(
            method=request.method,
            endpoint=request.endpoint or "unknown",
        )
    
    @app.after_request
    def after_request(response):
        duration = time.time() - getattr(g, "metrics_start_time", time.time())
        endpoint = request.endpoint or "unknown"
        method = request.method
        status = str(response.status_code)
        
        metrics.http_requests_total.inc(
            method=method,
            endpoint=endpoint,
            status=status,
        )
        metrics.http_request_duration_seconds.observe(
            duration,
            method=method,
            endpoint=endpoint,
        )
        metrics.http_requests_in_progress.dec(
            method=method,
            endpoint=endpoint,
        )
        
        return response


def metrics_endpoint() -> Response:
    """Prometheus metrics endpoint handler."""
    metrics = get_metrics()
    return Response(
        metrics.to_prometheus_format(),
        mimetype="text/plain; charset=utf-8",
    )
