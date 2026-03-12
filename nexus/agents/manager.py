"""
Agent Manager — Orchestrates all agents, runs them in parallel.
Central control point for starting, stopping, monitoring agents.
Integrates with the nexus CRM for unified activity tracking.
"""
import asyncio
import json
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from agents.base import BaseAgent
from agents.config import AGENT_STATE_DIR, load_nexus_config
from agents.content_agent import ContentAgent
from agents.facebook_agent import FacebookAgent
from agents.instagram_agent import InstagramAgent


class AgentManager:
    """Manages all Zoar agents — start, stop, monitor."""

    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._status_file = AGENT_STATE_DIR / "manager_status.json"

    def register(self, agent: BaseAgent):
        """Register an agent with the manager."""
        self.agents[agent.agent_id] = agent
        print(f"[Manager] Registered agent: {agent.agent_id} ({agent.agent_type})")

    async def start_agent(self, agent_id: str):
        """Start a specific agent as a background task."""
        if agent_id not in self.agents:
            print(f"[Manager] Unknown agent: {agent_id}")
            return

        agent = self.agents[agent_id]

        if agent_id in self._tasks and not self._tasks[agent_id].done():
            print(f"[Manager] Agent {agent_id} already running")
            return

        task = asyncio.create_task(self._run_agent(agent))
        self._tasks[agent_id] = task
        print(f"[Manager] Started agent: {agent_id}")

    async def _run_agent(self, agent: BaseAgent):
        """Wrapper to run agent with error handling."""
        try:
            await agent.start()
        except asyncio.CancelledError:
            agent.log("Agent cancelled")
        except Exception as e:
            agent.log(f"Agent crashed: {e}", "ERROR")
        finally:
            agent.stop()
            if hasattr(agent, 'cleanup'):
                try:
                    await agent.cleanup()
                except Exception:
                    pass

    async def stop_agent(self, agent_id: str):
        """Stop a specific agent."""
        if agent_id in self.agents:
            self.agents[agent_id].stop()
        if agent_id in self._tasks:
            self._tasks[agent_id].cancel()
            try:
                await self._tasks[agent_id]
            except asyncio.CancelledError:
                pass
            print(f"[Manager] Stopped agent: {agent_id}")

    async def start_all(self):
        """Start all registered agents."""
        self._running = True
        self._save_status()

        for agent_id in self.agents:
            await self.start_agent(agent_id)
            # Stagger agent starts by 10-30 seconds
            await asyncio.sleep(15)

        print(f"[Manager] All {len(self.agents)} agents started")

    async def stop_all(self):
        """Stop all agents gracefully."""
        self._running = False
        for agent_id in list(self._tasks.keys()):
            await self.stop_agent(agent_id)
        self._save_status()
        print("[Manager] All agents stopped")

    def get_status(self) -> dict:
        """Get status of all agents."""
        status = {
            "running": self._running,
            "agents": {},
            "total_cost": 0,
        }
        for agent_id, agent in self.agents.items():
            cost = agent.get_cost_estimate()
            is_running = agent_id in self._tasks and not self._tasks[agent_id].done()
            status["agents"][agent_id] = {
                "type": agent.agent_type,
                "running": is_running,
                "state": agent._state,
                "cost": cost,
            }
            status["total_cost"] += cost["estimated_cost_usd"]
        return status

    def _save_status(self):
        try:
            status = self.get_status()
            status["last_updated"] = datetime.now().isoformat()
            self._status_file.write_text(json.dumps(status, indent=2))
        except Exception:
            pass

    # ── Quick Actions (no browser needed) ──────────────────────────────────

    async def generate_weekly_content(self) -> list:
        """Generate a week's worth of social media content."""
        content_agent = ContentAgent()
        posts = await content_agent.generate_content_batch(count=7)
        cost = content_agent.get_cost_estimate()
        print(f"[Manager] Generated {len(posts)} posts. Cost: ${cost['estimated_cost_usd']:.4f}")
        return posts

    async def generate_comment_templates(self, count: int = 10) -> list:
        """Pre-generate comment templates for different scenarios."""
        content_agent = ContentAgent()
        templates = []

        scenarios = [
            ("Someone asking about outdoor wedding restrooms", "high"),
            ("Bride asking for vendor recommendations in LA", "medium"),
            ("Event planner discussing outdoor event logistics", "medium"),
            ("Someone sharing their outdoor wedding photos", "general"),
            ("Construction company asking about job site facilities", "high"),
            ("Film producer asking about on-set amenities", "high"),
            ("Someone asking about portable restroom options", "high"),
            ("Party planner sharing outdoor party tips", "medium"),
            ("Wedding venue asking about recommended vendors", "medium"),
            ("Someone complaining about porta-potties at events", "high"),
        ]

        for scenario, intent in scenarios[:count]:
            comment = await content_agent.generate_comment(
                post_context=scenario,
                intent_level=intent,
            )
            templates.append({
                "scenario": scenario,
                "intent": intent,
                "comment": comment,
                "generated_at": datetime.now().isoformat(),
            })

        # Save templates
        templates_file = AGENT_STATE_DIR / "comment_templates.json"
        templates_file.write_text(json.dumps(templates, indent=2))
        print(f"[Manager] Generated {len(templates)} comment templates")
        return templates


# ── CLI Entry Point ────────────────────────────────────────────────────────

async def main():
    """Main entry point for running agents from command line."""
    import argparse
    parser = argparse.ArgumentParser(description="Zoar Agent Manager")
    parser.add_argument("action", choices=[
        "start-all", "start-fb", "start-ig",
        "content", "templates", "status", "stop"
    ])
    parser.add_argument("--headless", action="store_true", help="Run browsers headless")
    args = parser.parse_args()

    manager = AgentManager()

    if args.action == "content":
        posts = await manager.generate_weekly_content()
        for p in posts:
            print(f"\n--- {p['post_type'].upper()} ({p['topic'][:50]}) ---")
            print(p['text'][:300])
        return

    if args.action == "templates":
        templates = await manager.generate_comment_templates()
        for t in templates:
            print(f"\n[{t['intent'].upper()}] {t['scenario']}")
            print(f"  → {t['comment']}")
        return

    if args.action == "status":
        status = manager.get_status()
        print(json.dumps(status, indent=2))
        return

    # Register agents based on action
    if args.action in ("start-all", "start-fb"):
        manager.register(FacebookAgent())
    if args.action in ("start-all", "start-ig"):
        manager.register(InstagramAgent())

    # Handle shutdown
    loop = asyncio.get_event_loop()

    def shutdown():
        print("\n[Manager] Shutdown signal received...")
        asyncio.ensure_future(manager.stop_all())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    try:
        await manager.start_all()
        # Keep running
        while manager._running:
            await asyncio.sleep(60)
            manager._save_status()
    except asyncio.CancelledError:
        pass
    finally:
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
