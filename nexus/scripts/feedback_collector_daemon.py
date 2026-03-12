#!/usr/bin/env python3
"""
Persistent feedback collector daemon.

Collects booked/lost outcomes from ~/.nexus/memory.db and writes:
  - positive_examples.jsonl
  - negative_examples.jsonl
  - weekly_training_delta.jsonl
into the configured feedback directory.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from core.feedback_collector import get_feedback_collector

LOG = logging.getLogger("feedback_collector_daemon")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_logging() -> None:
    log_dir = Path.home() / ".nexus" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "feedback_collector.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [FeedbackCollector] %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(str(log_path), mode="a")],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run feedback collector daemon.")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--delta-every-seconds", type=int, default=3600)
    args = parser.parse_args()

    _setup_logging()
    collector = get_feedback_collector()
    poll = max(10, int(args.poll_seconds))
    delta_every = max(60, int(args.delta_every_seconds))
    LOG.info("starting daemon poll=%ss delta_every=%ss dir=%s", poll, delta_every, collector.feedback_dir)

    last_delta = 0.0
    while True:
        try:
            result = collector.collect_once()
            LOG.info("collect_once=%s", json.dumps(result, ensure_ascii=False))
            now = time.time()
            if now - last_delta >= delta_every:
                delta = collector.export_weekly_delta()
                LOG.info("weekly_delta=%s", json.dumps(delta, ensure_ascii=False))
                last_delta = now
        except Exception as e:
            LOG.error("collector error: %s", e)
        time.sleep(poll)


if __name__ == "__main__":
    raise SystemExit(main())
