#!/usr/bin/env python3
"""
Agent Runner — Standalone script to run Zoar agents continuously.
Can be run via launchd (macOS) for 24/7 operation.

Usage:
    python3 run_agents.py --mode content     # Generate content only (no browser)
    python3 run_agents.py --mode research    # Lead research only (no browser)
    python3 run_agents.py --mode facebook    # Facebook engagement (browser)
    python3 run_agents.py --mode instagram   # Instagram engagement (browser)
    python3 run_agents.py --mode all         # All agents
"""
import sys
import os
import asyncio
import argparse
import signal
import json
from pathlib import Path
from datetime import datetime

# Add nexus root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.config import AGENT_STATE_DIR, load_nexus_config
from agents.content_agent import ContentAgent
from agents.lead_finder import LeadFinderAgent


async def run_content_loop():
    """Generate social media content on a schedule."""
    agent = ContentAgent()
    print(f"[{datetime.now()}] Content agent started")

    while True:
        try:
            # Generate a week's content
            posts = await agent.generate_content_batch(count=7)
            print(f"[{datetime.now()}] Generated {len(posts)} posts")

            cost = agent.get_cost_estimate()
            print(f"[{datetime.now()}] Running cost: ${cost['estimated_cost_usd']:.4f}")

            # Wait 24 hours before next batch
            await asyncio.sleep(86400)
        except Exception as e:
            print(f"[{datetime.now()}] Content error: {e}")
            await asyncio.sleep(3600)


async def run_research_loop():
    """Run lead research on a schedule."""
    agent = LeadFinderAgent()
    print(f"[{datetime.now()}] Lead research agent started")

    while True:
        try:
            leads = await agent.run_research_cycle()
            print(f"[{datetime.now()}] Research cycle: found {len(leads)} leads")

            cost = agent.get_cost_estimate()
            print(f"[{datetime.now()}] Running cost: ${cost['estimated_cost_usd']:.4f}")

            # Research every 6 hours
            await asyncio.sleep(21600)
        except Exception as e:
            print(f"[{datetime.now()}] Research error: {e}")
            await asyncio.sleep(3600)


async def run_analyst_loop():
    """Run analyst agent on a schedule."""
    from agents.analyst_agent import AnalystAgent
    agent = AnalystAgent()
    print(f"[{datetime.now()}] Analyst agent started")

    while True:
        try:
            insights = await agent.run_analysis_cycle()
            print(f"[{datetime.now()}] Analysis cycle: {len(insights)} insights produced")

            cost = agent.get_cost_estimate()
            print(f"[{datetime.now()}] Running cost: ${cost['estimated_cost_usd']:.4f}")

            # Analyze every 2 hours
            await asyncio.sleep(7200)
        except Exception as e:
            print(f"[{datetime.now()}] Analyst error: {e}")
            await asyncio.sleep(600)


async def run_facebook_loop():
    """Run Facebook engagement agent."""
    from agents.facebook_agent import FacebookAgent
    agent = FacebookAgent()
    await agent.start()


async def run_instagram_loop():
    """Run Instagram engagement agent."""
    from agents.instagram_agent import InstagramAgent
    agent = InstagramAgent()
    await agent.start()


async def main(mode: str):
    """Main entry point."""
    print(f"\n{'='*50}")
    print(f"Zoar Agent System — Mode: {mode}")
    print(f"Started: {datetime.now()}")
    print(f"{'='*50}\n")

    tasks = []

    if mode in ("content", "all"):
        tasks.append(asyncio.create_task(run_content_loop()))
    if mode in ("research", "all"):
        tasks.append(asyncio.create_task(run_research_loop()))
    if mode in ("analyst", "all"):
        tasks.append(asyncio.create_task(run_analyst_loop()))
    if mode in ("facebook", "all"):
        tasks.append(asyncio.create_task(run_facebook_loop()))
    if mode in ("instagram", "all"):
        tasks.append(asyncio.create_task(run_instagram_loop()))

    if not tasks:
        print(f"Unknown mode: {mode}")
        return

    # Handle shutdown
    shutdown_event = asyncio.Event()

    def handle_signal():
        print("\nShutdown signal received...")
        shutdown_event.set()
        for t in tasks:
            t.cancel()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass

    print(f"\n[{datetime.now()}] Agent system stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Zoar Agent Runner")
    parser.add_argument("--mode", default="content",
                       choices=["content", "research", "analyst", "facebook", "instagram", "all"])
    args = parser.parse_args()
    asyncio.run(main(args.mode))
