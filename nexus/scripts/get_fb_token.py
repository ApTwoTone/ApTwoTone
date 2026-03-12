#!/usr/bin/env python3
"""
Quick OAuth token generator for Facebook Marketing API.
Opens browser for auth, captures the code, exchanges for token.
"""
import http.server
import json
import sys
import threading
import urllib.parse
import webbrowser

import requests

APP_ID = "2478340775915445"
APP_SECRET = "d40242e2943af95f77e7eaa62c677c9a"
REDIRECT_URI = "http://localhost:8888/callback"
SCOPES = "ads_management,ads_read,pages_manage_ads,leads_retrieval,pages_read_engagement,pages_manage_posts"

auth_code = None
server_done = threading.Event()


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
            <html><body style="font-family:sans-serif;text-align:center;padding:50px">
            <h1>&#10004; Authorization successful!</h1>
            <p>You can close this tab. The token is being generated...</p>
            </body></html>
            """)
            server_done.set()
        elif "error" in params:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            error = params.get("error_description", ["Unknown error"])[0]
            self.wfile.write(f"""
            <html><body style="font-family:sans-serif;text-align:center;padding:50px">
            <h1>&#10060; Authorization failed</h1>
            <p>{error}</p>
            </body></html>
            """.encode())
            server_done.set()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Waiting for callback...</body></html>")

    def log_message(self, format, *args):
        pass  # Suppress logs


def main():
    # Start local server
    server = http.server.HTTPServer(("localhost", 8888), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    # Build OAuth URL
    oauth_url = (
        f"https://www.facebook.com/v21.0/dialog/oauth?"
        f"client_id={APP_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&scope={SCOPES}"
        f"&response_type=code"
    )

    print(f"\n{'='*60}")
    print("  Facebook OAuth Token Generator")
    print(f"{'='*60}")
    print(f"\nOpening browser for authorization...")
    print(f"\nIf the browser doesn't open, visit:\n{oauth_url}\n")

    # Open browser
    webbrowser.open(oauth_url)

    # Wait for callback
    print("Waiting for authorization...")
    server_done.wait(timeout=120)
    server.shutdown()

    if not auth_code:
        print("\nERROR: No authorization code received.")
        sys.exit(1)

    print("Authorization code received! Exchanging for token...")

    # Exchange code for token
    resp = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "client_id": APP_ID,
            "client_secret": APP_SECRET,
            "redirect_uri": REDIRECT_URI,
            "code": auth_code,
        },
    )
    data = resp.json()

    if "error" in data:
        print(f"\nERROR: {data['error'].get('message', 'Unknown')}")
        sys.exit(1)

    token = data["access_token"]
    print(f"\n{'='*60}")
    print("  ACCESS TOKEN (with ads_management)")
    print(f"{'='*60}")
    print(f"\n{token}\n")

    # Verify permissions
    print("Verifying permissions...")
    perms_resp = requests.get(
        "https://graph.facebook.com/v21.0/me/permissions",
        params={"access_token": token},
    )
    perms_data = perms_resp.json()
    granted = [p["permission"] for p in perms_data.get("data", []) if p.get("status") == "granted"]
    print(f"Granted: {', '.join(granted)}")

    if "ads_management" in granted:
        print("\n✅ ads_management permission confirmed!")
    else:
        print("\n⚠️  ads_management NOT in granted permissions")

    # Save token
    token_path = "/Users/kai/Downloads/fb_token_ads.txt"
    with open(token_path, "w") as f:
        f.write(token)
    print(f"\nToken saved to: {token_path}")

    # Also try to get a long-lived token
    print("\nExchanging for long-lived token...")
    ll_resp = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": APP_ID,
            "client_secret": APP_SECRET,
            "fb_exchange_token": token,
        },
    )
    ll_data = ll_resp.json()
    if "access_token" in ll_data:
        ll_token = ll_data["access_token"]
        expires = ll_data.get("expires_in", "unknown")
        print(f"Long-lived token obtained (expires in {expires}s)")
        with open(token_path, "w") as f:
            f.write(ll_token)
        print(f"Long-lived token saved to: {token_path}")
    else:
        print(f"Could not get long-lived token: {ll_data}")


if __name__ == "__main__":
    main()
