"""Lightweight HTTP health-check endpoint for PaaS keep-alive (Render, Railway)."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)


def _make_handler(health_path: str) -> type[BaseHTTPRequestHandler]:
    """Create a handler class with the given health path."""

    class _HealthHandler(BaseHTTPRequestHandler):
        """Responds 200 to the health path, 404 to everything else."""

        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler convention
            if self.path == health_path:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Suppress default stderr logging — use our logger instead."""
            logger.debug("Health check: %s", format % args)

    return _HealthHandler


def start_health_check(
    port: int = 10000,
    path: str = "/health",
) -> tuple[HTTPServer, threading.Thread]:
    """Start a health-check HTTP server on a daemon thread.

    Args:
        port: Port to listen on (default 10000 for Render).
        path: URL path for the health endpoint.

    Returns:
        Tuple of (server, thread) for shutdown control.
    """
    handler = _make_handler(path)
    server = HTTPServer(("0.0.0.0", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.info("Health check listening on port %d at %s", port, path)
    return server, thread
