#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(v: Any) -> str:
    try:
        return str(v if v is not None else "").strip()
    except Exception:
        return ""


def _append_log(path: Path, row: Dict[str, Any]) -> None:
    payload: List[Dict[str, Any]] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = []
    payload.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _send_telegram(text: str, token: str, chat_id: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urlrequest.Request(url, method="POST", data=body, headers={"Content-Type": "application/json"})
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            return {"ok": True, "status": resp.status}
    except (HTTPError, URLError, TimeoutError) as e:
        return {"ok": False, "error": str(e)}


def _build_tarball(run_root: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tar_path = out_dir / f"nexus_brain_export_{stamp}.tar.gz"
    include = [
        run_root / "runpod_lab" / "exports",
        run_root / "runpod_lab" / "training_artifacts",
        run_root / "runpod_lab" / "training_data",
        Path("/workspace/nexus_brain_lab/feedback"),
    ]
    with tarfile.open(tar_path, "w:gz") as tar:
        for p in include:
            if p.exists():
                tar.add(p, arcname=str(p).lstrip("/"))
    return tar_path


def run_once(args: argparse.Namespace) -> Dict[str, Any]:
    run_root = Path(args.run_root).resolve()
    out_dir = Path(args.output_dir).resolve()
    log_path = Path(args.log_path).resolve()
    tar_path = _build_tarball(run_root, out_dir)
    summary = {
        "created_at": _now(),
        "run_root": str(run_root),
        "tarball": str(tar_path),
        "size_bytes": tar_path.stat().st_size if tar_path.exists() else 0,
        "download_url": str(tar_path),
    }
    _append_log(log_path, summary)
    token = _safe_text(os.environ.get("TELEGRAM_BOT_TOKEN") or args.telegram_token)
    chat_id = _safe_text(args.telegram_chat_id or "8540603351")
    if token:
        msg = (
            "Nexus Brain Safe Export Complete\n"
            f"Time: {summary['created_at']}\n"
            f"Tarball: {summary['tarball']}\n"
            f"Size: {summary['size_bytes']} bytes\n"
            f"URL: {summary['download_url']}"
        )
        summary["telegram"] = _send_telegram(msg, token=token, chat_id=chat_id)
    else:
        summary["telegram"] = {"ok": False, "error": "missing TELEGRAM_BOT_TOKEN"}
    _append_log(log_path, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe export of Runpod brain artifacts")
    parser.add_argument("--run-root", default="/workspace/nexus_brain_lab/run_14")
    parser.add_argument("--output-dir", default="/workspace/nexus_brain_lab/exports_archive")
    parser.add_argument("--log-path", default="/workspace/nexus_brain_lab/export_log.json")
    parser.add_argument("--telegram-token", default="")
    parser.add_argument("--telegram-chat-id", default="8540603351")
    parser.add_argument("--daemon", action="store_true", help="Run every 2 hours")
    parser.add_argument("--interval-sec", type=int, default=7200)
    args = parser.parse_args()

    if not args.daemon:
        print(json.dumps(run_once(args), ensure_ascii=False, indent=2))
        return

    while True:
        result = run_once(args)
        print(json.dumps(result, ensure_ascii=False))
        time.sleep(max(300, int(args.interval_sec)))


if __name__ == "__main__":
    main()
