#!/usr/bin/env python3
"""
Cloud Failover Agent Runner — Runs critical agents on GitHub Actions.

Called by the cloud-failover.yml workflow when Mac Mini is offline.
Each agent is a self-contained function that calls free AI model APIs.
"""
import os
import sys
import json
from urllib.request import Request, urlopen


def call_gemini(prompt):
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("No GEMINI_API_KEY — skipping")
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_groq(prompt):
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("No GROQ_API_KEY — skipping")
        return None
    payload = json.dumps({
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2048,
    }).encode()
    req = Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "nexus-cloud-failover/1.0",
        },
    )
    with urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def run_lead_accelerator():
    prompt = (
        "You are the Lead Accelerator for Zoar Bathroom Rentals. "
        "The primary system (Mac Mini) is offline. Cloud failover is active. "
        "Report: Cloud failover active. Ready to process leads via webhook. "
        "Pricing: Tier 1 (0-10mi) $1,000, Tier 2 (10-20mi) $1,200+mileage, "
        "Tier 3 (20+mi) $1,500+mileage. Deposit: $160."
    )
    result = call_gemini(prompt)
    if result:
        print(f"Lead Accelerator: {result[:500]}")
    return result


def run_ad_copywriter():
    prompt = (
        "Write 2 Facebook ad copy variations for Zoar Bathroom Rentals "
        "targeting weddings in San Fernando Valley. Use 'Starting at $999'. "
        "Say 'Delivery and setup included'. Never say 'all-inclusive'. "
        "Use luxury trailer vs porta potty contrast. "
        "Each: headline (<40 chars), primary text (<125 chars), CTA."
    )
    result = call_groq(prompt)
    if result:
        print(f"Ad Copywriter: {result[:500]}")
    return result


def send_notification():
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("No Telegram config — skipping notification")
        return
    msg = (
        "CLOUD FAILOVER ACTIVE\n\n"
        "Mac Mini offline. GitHub Actions running critical agents.\n"
        "Lead Accelerator: standby\n"
        "Ad Copy: generated\n\n"
        "System will auto-recover when Mac comes back online."
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": msg}).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        resp.read()
    print("Telegram notification sent")


AGENTS = {
    "lead-accelerator": run_lead_accelerator,
    "ad-copywriter": run_ad_copywriter,
    "notify": send_notification,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <agent-name>")
        print(f"Available: {', '.join(AGENTS.keys())}")
        sys.exit(1)

    agent = sys.argv[1]
    if agent not in AGENTS:
        print(f"Unknown agent: {agent}")
        sys.exit(1)

    try:
        AGENTS[agent]()
    except Exception as e:
        print(f"Agent {agent} failed: {e}")
        sys.exit(1)
