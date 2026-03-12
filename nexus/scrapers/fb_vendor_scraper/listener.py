"""
FB Vendor Scraper — HTTP listener for remote triggers.
Runs on port 7899 on the Mac. Accepts commands from Nexus server.
"""
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from config import LISTENER_PORT, ALLOWED_IPS, GROUPS_FILE

log = logging.getLogger("fb_scraper")

# Shared state flags — the main scraper checks these
_scrape_requested = threading.Event()
_stop_requested = threading.Event()


def is_scrape_requested() -> bool:
    return _scrape_requested.is_set()


def clear_scrape_request():
    _scrape_requested.clear()


def is_stop_requested() -> bool:
    return _stop_requested.is_set()


def clear_stop_request():
    _stop_requested.clear()


def request_stop():
    _stop_requested.set()


class ScrapeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.debug(f"[Listener] {format % args}")

    def _check_ip(self) -> bool:
        client_ip = self.client_address[0]
        if client_ip not in ALLOWED_IPS:
            log.warning(f"Rejected request from {client_ip}")
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'{"error":"forbidden"}')
            return False
        return True

    def _send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_POST(self):
        if not self._check_ip():
            return

        if self.path == "/start_scrape":
            _scrape_requested.set()
            _stop_requested.clear()
            log.info("Scrape triggered via HTTP")
            self._send_json({"ok": True, "message": "Scrape started"})

        elif self.path == "/stop_scrape":
            _stop_requested.set()
            log.info("Stop requested via HTTP")
            self._send_json({"ok": True, "message": "Stop requested"})

        elif self.path == "/add_group":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                url = body.get("url", "").strip()
                if not url or "facebook.com/groups/" not in url:
                    self._send_json({"error": "Invalid group URL"}, 400)
                    return
                groups = _load_groups()
                # Check duplicate
                if any(g["url"] == url for g in groups):
                    self._send_json({"ok": True, "name": "already added", "message": "Group already in list"})
                    return
                name = url.rstrip("/").split("/")[-1].replace("-", " ").title()
                groups.append({
                    "url": url,
                    "name": name,
                    "member_count": "?",
                    "description": "",
                    "posts_scraped": 0,
                    "last_scraped": "",
                    "last_post_date": "",
                })
                _save_groups(groups)
                log.info(f"Added group: {name} ({url})")
                self._send_json({"ok": True, "name": name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif self.path == "/remove_group":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                url = body.get("url", "").strip()
                groups = _load_groups()
                groups = [g for g in groups if g["url"] != url]
                _save_groups(groups)
                log.info(f"Removed group: {url}")
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        else:
            self._send_json({"error": "Unknown endpoint"}, 404)

    def do_GET(self):
        if not self._check_ip():
            return

        if self.path == "/groups":
            groups = _load_groups()
            self._send_json({"groups": groups})

        elif self.path == "/status":
            self._send_json({
                "running": _scrape_requested.is_set(),
                "stop_requested": _stop_requested.is_set(),
            })

        else:
            self._send_json({"error": "Unknown endpoint"}, 404)


def _load_groups() -> list:
    if GROUPS_FILE.exists():
        try:
            return json.loads(GROUPS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_groups(groups: list):
    GROUPS_FILE.write_text(json.dumps(groups, indent=2))


def start_listener():
    """Start the HTTP listener in a background daemon thread."""
    server = HTTPServer(("0.0.0.0", LISTENER_PORT), ScrapeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Listener started on port {LISTENER_PORT}")
    return server
