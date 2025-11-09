import time
from collections import defaultdict
from typing import Dict

# --- Rate Limiter ---

class RateLimiter:
    """A simple in-memory rate limiter."""
    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self.interval = 60.0 / requests_per_minute
        self.timestamps: Dict[str, float] = defaultdict(float)

    def wait(self, key: str = "default"):
        """Waits if necessary to respect the rate limit for a given key."""
        last_request_time = self.timestamps.get(key, 0)
        elapsed = time.monotonic() - last_request_time

        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)

        self.timestamps[key] = time.monotonic()

# --- Circuit Breaker ---

class CircuitBreaker:
    """A simple in-memory circuit breaker."""
    def __init__(self, failure_threshold: int, recovery_timeout: int):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_counts: Dict[str, int] = defaultdict(int)
        self.open_until: Dict[str, float] = defaultdict(float)

    def is_open(self, key: str = "default") -> bool:
        """Checks if the circuit is open for a given key."""
        if self.open_until.get(key, 0) > time.monotonic():
            return True
        return False

    def record_failure(self, key: str = "default"):
        """Records a failure and opens the circuit if the threshold is met."""
        self.failure_counts[key] += 1
        if self.failure_counts[key] >= self.failure_threshold:
            self.open_until[key] = time.monotonic() + self.recovery_timeout
            print(f"Circuit breaker for '{key}' opened for {self.recovery_timeout} seconds.")

    def record_success(self, key: str = "default"):
        """Resets the failure count for a given key."""
        self.failure_counts[key] = 0
        self.open_until[key] = 0
