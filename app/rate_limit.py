import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class RateLimiter:
    """
    Simple in-memory sliding-window rate limiter, keyed by client IP.
    Deliberately in-process rather than Redis-backed — this app runs
    as a single process at family/friend scale (see DEPLOY.md), so
    in-memory state is enough, and it avoids adding infrastructure for
    what's meant as a basic guard against automated login/registration
    abuse, not a hardened defense. Resets on every restart; that's an
    acceptable trade-off at this scale.
    """

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Raises HTTPException(429) if `key` has already hit the
        limit within the current window; otherwise records this
        attempt and returns normally."""
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()

        if len(hits) >= self.max_requests:
            retry_after = int(self.window_seconds - (now - hits[0])) + 1
            raise HTTPException(
                status_code=429,
                detail="Too many attempts. Please wait before trying again.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)


def get_client_ip(request: Request) -> str:
    """
    request.client.host alone isn't the real visitor — it's whatever
    directly connected to uvicorn, which per DEPLOY.md is either nginx
    (sets X-Real-IP) or, when fronted by Cloudflare Tunnel instead of
    a local nginx, cloudflared itself (sets Cf-Connecting-Ip to the
    original visitor's address). Checks both, preferring
    Cf-Connecting-Ip when present since that's the more specific
    signal on a Cloudflare-fronted deployment; falls back to
    request.client.host for local/direct access (e.g. hitting uvicorn
    straight from a dev machine).
    """
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"
