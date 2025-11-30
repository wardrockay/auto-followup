"""
Circuit Breaker Pattern.

Protects external service calls from cascading failures.
"""

import time
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from threading import Lock
from typing import Any, Callable, Optional, Type, Tuple

from auto_followup.infrastructure.logging import get_logger


logger = get_logger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5       # Failures before opening
    success_threshold: int = 2       # Successes to close from half-open
    timeout_seconds: float = 30.0    # Time before trying again
    excluded_exceptions: Tuple[Type[Exception], ...] = ()  # Don't count these as failures


class CircuitBreaker:
    """
    Circuit breaker for external service calls.
    
    Prevents cascading failures by temporarily rejecting requests
    when a service is failing.
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = Lock()
    
    @property
    def state(self) -> CircuitState:
        """Get current state, checking for timeout."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info(
                        f"Circuit breaker '{self._name}' entering half-open state",
                        extra={"extra_fields": {"circuit": self._name}}
                    )
            return self._state
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try again."""
        if self._last_failure_time is None:
            return True
        elapsed = time.time() - self._last_failure_time
        return elapsed >= self._config.timeout_seconds
    
    def _record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info(
                        f"Circuit breaker '{self._name}' closed after recovery",
                        extra={"extra_fields": {
                            "circuit": self._name,
                            "success_count": self._success_count,
                        }}
                    )
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0
    
    def _record_failure(self, exception: Exception) -> None:
        """Record a failed call."""
        # Check if this exception should be excluded
        if isinstance(exception, self._config.excluded_exceptions):
            return
        
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                # Single failure in half-open reopens the circuit
                self._state = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker '{self._name}' reopened after half-open failure",
                    extra={"extra_fields": {
                        "circuit": self._name,
                        "error": str(exception),
                    }}
                )
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        f"Circuit breaker '{self._name}' opened after {self._failure_count} failures",
                        extra={"extra_fields": {
                            "circuit": self._name,
                            "failure_count": self._failure_count,
                            "error": str(exception),
                        }}
                    )
    
    def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """
        Execute a function through the circuit breaker.
        
        Args:
            func: Function to execute.
            *args: Function arguments.
            **kwargs: Function keyword arguments.
            
        Returns:
            Function result.
            
        Raises:
            CircuitBreakerOpenError: If circuit is open.
            Exception: Any exception from the function.
        """
        state = self.state
        
        if state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self._name}' is open"
            )
        
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure(e)
            raise
    
    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            logger.info(
                f"Circuit breaker '{self._name}' manually reset",
                extra={"extra_fields": {"circuit": self._name}}
            )


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


# Global circuit breakers registry
_circuit_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = Lock()


def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """Get or create a circuit breaker by name."""
    with _registry_lock:
        if name not in _circuit_breakers:
            _circuit_breakers[name] = CircuitBreaker(name, config)
        return _circuit_breakers[name]


def circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> Callable:
    """
    Decorator to protect a function with a circuit breaker.
    
    Usage:
        @circuit_breaker("odoo-api")
        def call_odoo():
            ...
    """
    def decorator(func: Callable) -> Callable:
        cb = get_circuit_breaker(name, config)
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return cb.call(func, *args, **kwargs)
        
        return wrapper
    
    return decorator
