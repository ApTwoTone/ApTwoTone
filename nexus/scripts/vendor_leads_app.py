#!/usr/bin/env python3
"""
Vendor Leads — Native macOS app launcher.
Opens a WebKit window pointing at the Nexus vendor-leads dashboard.
"""
import sys
import time
import urllib.request

SERVER_URL = "http://localhost:7860"
DASHBOARD_URL = f"{SERVER_URL}/vendor-leads/"


def wait_for_server(timeout=30):
    """Wait until the Nexus server is responding."""
    for _ in range(timeout):
        try:
            urllib.request.urlopen(f"{SERVER_URL}/api/health", timeout=2)
            return True
        except Exception:
            time.sleep(1)
    return False


def main():
    import webview

    if not wait_for_server():
        webview.create_window(
            "Vendor Leads",
            html="<html><body style='background:#0b1120;color:#e8eaf0;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;'>"
                 "<div style='text-align:center;'><h2 style='color:#C5A55A;'>Nexus Server Not Running</h2>"
                 "<p>Start the server first:<br><code>cd ~/nexus && python3 server.py</code></p></div></body></html>",
            width=500, height=300,
        )
        webview.start()
        return

    window = webview.create_window(
        "Vendor Leads — Zoar",
        DASHBOARD_URL,
        width=1440,
        height=900,
        min_size=(1024, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
