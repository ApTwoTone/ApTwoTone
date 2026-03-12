#!/usr/bin/env python3
"""
Facebook Lead Sync — Pull leads from FB Lead Forms into Nexus CRM.

Exchanges system user token for page token, fetches all lead forms,
downloads leads, and imports via LeadService (with dedup by leadgen_id).

Can run as one-shot import or as a daemon polling every N minutes.
"""
import json
import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.services.lead_service import LeadService

log = logging.getLogger("fb_lead_sync")

CONFIG_PATH = Path.home() / ".nexus" / "config.json"
GRAPH_API = "https://graph.facebook.com/v21.0"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_page_token(cfg: dict) -> str:
    """Exchange system user token for a real page access token."""
    page_id = cfg.get("fb_page_id", "")
    system_token = cfg.get("fb_page_access_token", "")
    if not page_id or not system_token:
        raise ValueError("Missing fb_page_id or fb_page_access_token in config")

    resp = requests.get(
        f"{GRAPH_API}/{page_id}",
        params={"fields": "access_token", "access_token": system_token},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise ValueError(f"No access_token in response: {data}")
    return token


def get_lead_forms(page_id: str, page_token: str) -> list:
    """List all lead gen forms on the page."""
    resp = requests.get(
        f"{GRAPH_API}/{page_id}/leadgen_forms",
        params={"access_token": page_token, "limit": 100},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_form_leads(form_id: str, page_token: str) -> list:
    """Download all leads from a specific form (paginated)."""
    all_leads = []
    url = f"{GRAPH_API}/{form_id}/leads"
    params = {"access_token": page_token, "limit": 500}

    while url:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_leads.extend(data.get("data", []))
        # Pagination
        paging = data.get("paging", {})
        url = paging.get("next")
        params = {}  # next URL includes params already

    return all_leads


def parse_fb_lead(lead: dict, form_name: str = "") -> dict:
    """Convert FB lead field_data into our CRM format."""
    fields = {}
    for item in lead.get("field_data", []):
        name = item.get("name", "").lower().strip()
        values = item.get("values", [])
        val = values[0] if values else ""
        fields[name] = val

    # Map FB field names to our schema
    full_name = fields.get("full_name", "") or fields.get("name", "")
    parts = full_name.split(None, 1)
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""

    return {
        "lead_uuid": lead.get("id", ""),  # Facebook leadgen_id for dedup
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "email": fields.get("email", ""),
        "phone": fields.get("phone_number", "") or fields.get("phone", ""),
        "source": "facebook_lead_ad",
        "form_id": lead.get("form_id", ""),
        "form_name": form_name,
        "event_date": fields.get("event_date", "") or fields.get("event_date_(mm/dd/yyyy)", ""),
        "event_type": fields.get("event_type", "") or fields.get("event type", ""),
        "event_city": fields.get("event_city", "") or fields.get("city", ""),
        "guest_count": fields.get("guest_count", "") or fields.get("estimated_guest_count", "") or 0,
        "ad_id": lead.get("ad_id", ""),
        "campaign_id": lead.get("campaign_id", ""),
        "notes": f"Imported from FB lead form: {form_name}. Created: {lead.get('created_time', '')}",
    }


def sync_leads(one_shot: bool = True, poll_minutes: int = 5) -> dict:
    """Pull all FB leads and import into CRM. Returns summary."""
    cfg = load_config()
    page_id = cfg.get("fb_page_id", "")

    log.info("Exchanging token for page %s...", page_id)
    page_token = get_page_token(cfg)

    log.info("Fetching lead forms...")
    forms = get_lead_forms(page_id, page_token)
    log.info("Found %d lead form(s)", len(forms))

    svc = LeadService()
    summary = {"forms": len(forms), "total_leads": 0, "new": 0, "existing": 0, "errors": 0, "new_leads": []}

    for form in forms:
        form_id = form.get("id", "")
        form_name = form.get("name", "Unknown Form")
        log.info("Processing form: %s (%s)", form_name, form_id)

        leads = get_form_leads(form_id, page_token)
        log.info("  %d leads in form", len(leads))
        summary["total_leads"] += len(leads)

        for fb_lead in leads:
            try:
                lead_data = parse_fb_lead(fb_lead, form_name)
                result = svc.create_lead(lead_data)

                if result.get("_deduplicated"):
                    summary["existing"] += 1
                    log.debug("  Dedup: %s (%s)", lead_data["full_name"], lead_data["email"])
                else:
                    summary["new"] += 1
                    summary["new_leads"].append({
                        "id": result.get("id"),
                        "name": lead_data["full_name"],
                        "email": lead_data["email"],
                        "phone": lead_data["phone"],
                        "event_type": lead_data["event_type"],
                        "event_date": lead_data["event_date"],
                        "created": fb_lead.get("created_time", ""),
                    })
                    log.info("  NEW LEAD: %s — %s — %s",
                             lead_data["full_name"], lead_data["email"], lead_data["phone"])
            except Exception as e:
                summary["errors"] += 1
                log.error("  Error importing lead %s: %s", fb_lead.get("id", "?"), e)

    return summary


def send_telegram_alert(summary: dict):
    """Notify Kai about new leads via Telegram."""
    if summary["new"] == 0:
        return

    try:
        from scripts.notify_telegram import send_telegram, load_config as tg_config
        cfg = tg_config()
        token = cfg.get("telegram_token", "")
        lines = [f"FB LEAD SYNC: {summary['new']} NEW lead(s) imported"]
        for lead in summary["new_leads"][:5]:
            lines.append(f"  {lead['name']} | {lead['phone']} | {lead['event_type']} | {lead['event_date']}")
        if summary["new"] > 5:
            lines.append(f"  ...and {summary['new'] - 5} more")
        lines.append(f"\nTotal in forms: {summary['total_leads']} | Already in CRM: {summary['existing']}")

        msg = "\n".join(lines)
        for cid in cfg.get("telegram_chat_ids", []):
            send_telegram(token, str(cid), msg)
        log.info("Telegram alert sent")
    except Exception as e:
        log.warning("Telegram alert failed: %s", e)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    daemon = "--daemon" in sys.argv
    poll = 5  # minutes

    for arg in sys.argv:
        if arg.startswith("--poll="):
            poll = int(arg.split("=")[1])

    while True:
        print("=" * 50)
        print("FB LEAD SYNC — Pulling leads from Facebook")
        print("=" * 50)

        try:
            summary = sync_leads()

            print(f"\nForms scanned: {summary['forms']}")
            print(f"Total leads in FB: {summary['total_leads']}")
            print(f"New imports: {summary['new']}")
            print(f"Already in CRM: {summary['existing']}")
            print(f"Errors: {summary['errors']}")

            if summary["new_leads"]:
                print("\nNew leads imported:")
                for lead in summary["new_leads"]:
                    print(f"  {lead['name']} | {lead['email']} | {lead['phone']} | "
                          f"{lead['event_type']} | {lead['event_date']}")

            send_telegram_alert(summary)

        except Exception as e:
            log.error("Sync failed: %s", e)
            import traceback
            traceback.print_exc()

        if not daemon:
            break

        print(f"\nSleeping {poll} minutes...")
        time.sleep(poll * 60)


if __name__ == "__main__":
    main()
