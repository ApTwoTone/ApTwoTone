"""
Nexus Brain Pipeline
--------------------

Builds a training-ready dataset + Runpod LoRA training pack from Nexus code/docs.
This is an internal prep pipeline (no outbound actions, no platform automation).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger("nexus_brain_pipeline")

DB_PATH = Path.home() / ".nexus" / "nexus_brain.db"
ROOT = Path.home() / "nexus"
if not ROOT.exists():
    ROOT = Path.cwd()
RUNS_ROOT = Path.home() / ".nexus" / "nexus_brain"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

DEFAULT_CFG: Dict[str, Any] = {
    "enabled": True,
    "include_paths": [
        "core",
        "integrations",
        "scripts",
        "server.py",
        "telegram",
        "website",
        "static/js",
    ],
    "exclude_dirs": [
        ".git",
        "venv",
        "node_modules",
        "outputs",
        "staging",
        "audit_screenshots",
        "test-screenshots",
        "__pycache__",
        ".playwright-cli",
        "VendorLeads.app",
    ],
    "extensions": [
        ".py",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".js",
        ".ts",
        ".tsx",
        ".css",
        ".html",
    ],
    "max_file_bytes": 260_000,
    "chunk_chars": 2400,
    "chunk_overlap": 220,
    "max_chunks": 22000,
    "valid_split": 0.06,
    "base_model": "Qwen/Qwen2.5-7B-Instruct",
    "lora_rank": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "epochs": 2,
    "learning_rate": 2e-4,
    "micro_batch_size": 2,
    "gradient_accumulation_steps": 8,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _safe_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_str(v: Any) -> str:
    try:
        return str(v or "")
    except Exception:
        return ""


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, indent=2)


class NexusBrainPipeline:
    def __init__(self) -> None:
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nexus_brain_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT DEFAULT 'running',
                    phase TEXT DEFAULT 'plan',
                    runtime_json TEXT DEFAULT '{}',
                    outputs_json TEXT DEFAULT '{}',
                    error TEXT DEFAULT '',
                    started_at TEXT DEFAULT (datetime('now')),
                    finished_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nexus_brain_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    level TEXT DEFAULT 'info',
                    phase TEXT DEFAULT '',
                    message TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nexus_brain_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    artifact_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    meta_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_runs_started ON nexus_brain_runs(started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_events_run ON nexus_brain_events(run_id, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_artifacts_run ON nexus_brain_artifacts(run_id, id DESC)")
            conn.commit()
        finally:
            conn.close()

    def _load_config(self) -> Dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text())
            except Exception:
                return {}
        return {}

    def _runtime(self, overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        cfg = self._load_config()
        rt = dict(DEFAULT_CFG)
        if isinstance(cfg.get("nexus_brain"), dict):
            rt.update(cfg.get("nexus_brain") or {})
        if isinstance(overrides, dict):
            rt.update(overrides)
        rt["max_file_bytes"] = max(10_000, _safe_int(rt.get("max_file_bytes"), 260_000))
        rt["chunk_chars"] = max(400, _safe_int(rt.get("chunk_chars"), 2400))
        rt["chunk_overlap"] = max(0, min(rt["chunk_chars"] - 50, _safe_int(rt.get("chunk_overlap"), 220)))
        rt["max_chunks"] = max(500, _safe_int(rt.get("max_chunks"), 22000))
        rt["valid_split"] = min(0.4, max(0.01, _safe_float(rt.get("valid_split"), 0.06)))
        rt["lora_rank"] = max(4, _safe_int(rt.get("lora_rank"), 16))
        rt["lora_alpha"] = max(8, _safe_int(rt.get("lora_alpha"), 32))
        rt["lora_dropout"] = min(0.2, max(0.0, _safe_float(rt.get("lora_dropout"), 0.05)))
        rt["epochs"] = max(1, _safe_int(rt.get("epochs"), 2))
        rt["micro_batch_size"] = max(1, _safe_int(rt.get("micro_batch_size"), 2))
        rt["gradient_accumulation_steps"] = max(1, _safe_int(rt.get("gradient_accumulation_steps"), 8))
        return rt

    def _is_excluded(self, p: Path, excludes: Sequence[str]) -> bool:
        low_parts = [part.lower() for part in p.parts]
        for e in excludes:
            ev = e.strip().lower()
            if ev and ev in low_parts:
                return True
        return False

    def _iter_files(self, rt: Dict[str, Any]) -> Iterable[Path]:
        include_paths = rt.get("include_paths") or DEFAULT_CFG["include_paths"]
        exts = {x.lower() for x in (rt.get("extensions") or DEFAULT_CFG["extensions"])}
        excludes = rt.get("exclude_dirs") or DEFAULT_CFG["exclude_dirs"]
        max_bytes = _safe_int(rt.get("max_file_bytes"), 260_000)
        seen: set[str] = set()
        for rel in include_paths:
            target = (ROOT / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
            if not target.exists():
                continue
            if target.is_file():
                if target.suffix.lower() in exts and target.stat().st_size <= max_bytes and not self._is_excluded(target, excludes):
                    key = str(target)
                    if key not in seen:
                        seen.add(key)
                        yield target
                continue
            for p in target.rglob("*"):
                if not p.is_file():
                    continue
                if self._is_excluded(p, excludes):
                    continue
                if p.suffix.lower() not in exts:
                    continue
                try:
                    if p.stat().st_size > max_bytes:
                        continue
                except Exception:
                    continue
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                yield p

    def _read_text(self, p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def _chunks(self, text: str, chunk_chars: int, overlap: int) -> List[str]:
        text = text.strip()
        if not text:
            return []
        if len(text) <= chunk_chars:
            return [text]
        out: List[str] = []
        step = max(120, chunk_chars - overlap)
        pos = 0
        while pos < len(text):
            chunk = text[pos : pos + chunk_chars].strip()
            if chunk:
                out.append(chunk)
            pos += step
        return out

    def plan(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rt = self._runtime((overrides or {}).get("nexus_brain") if isinstance(overrides, dict) else None)
        files = list(self._iter_files(rt))
        byte_total = 0
        est_chunks = 0
        sample_files: List[str] = []
        for p in files:
            try:
                b = p.stat().st_size
            except Exception:
                b = 0
            byte_total += b
            est_chunks += max(1, int((b / max(1, rt["chunk_chars"] - rt["chunk_overlap"])) + 1))
            if len(sample_files) < 20:
                sample_files.append(str(p))
        est_chunks = min(rt["max_chunks"], est_chunks)
        est_tokens = int((byte_total / 4.0) * 0.90)
        return {
            "ok": True,
            "runtime": rt,
            "estimate": {
                "files": len(files),
                "bytes_total": byte_total,
                "chunks": est_chunks,
                "approx_tokens": est_tokens,
                "train_examples": int(est_chunks * (1 - rt["valid_split"])),
                "valid_examples": int(est_chunks * rt["valid_split"]),
            },
            "sample_files": sample_files,
        }

    async def start(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rt = self._runtime((overrides or {}).get("nexus_brain") if isinstance(overrides, dict) else None)
        run_id = self._create_run(rt)
        start_ts = time.time()
        try:
            self._set_phase(run_id, "dataset")
            self._event(run_id, "info", "dataset", "Building dataset")
            dataset = await self._build_dataset(run_id, rt)
            self._event(run_id, "info", "dataset", "Dataset built", dataset["stats"])

            self._set_phase(run_id, "pack")
            self._event(run_id, "info", "pack", "Writing Runpod training pack")
            pack = self._write_runpod_pack(run_id, rt, dataset)
            self._event(run_id, "info", "pack", "Training pack ready", {"path": str(pack["pack_dir"])})

            outputs = {
                "dataset_train_jsonl": str(dataset["train_jsonl"]),
                "dataset_valid_jsonl": str(dataset["valid_jsonl"]),
                "rag_corpus_jsonl": str(dataset["rag_jsonl"]),
                "train_examples": dataset["stats"]["train_examples"],
                "valid_examples": dataset["stats"]["valid_examples"],
                "source_files": dataset["stats"]["source_files"],
                "runpod_pack_dir": str(pack["pack_dir"]),
                "runpod_launch_script": str(pack["launch_sh"]),
                "runpod_train_script": str(pack["train_py"]),
                "duration_ms": int((time.time() - start_ts) * 1000),
            }
            self._finish_run(run_id, "completed", outputs=outputs)
            return {"ok": True, "run_id": run_id, "outputs": outputs}
        except Exception as e:
            self._event(run_id, "error", self._phase(run_id), "Pipeline failed", {"error": str(e)})
            self._finish_run(run_id, "failed", error=str(e), outputs={})
            return {"ok": False, "run_id": run_id, "error": str(e)}

    async def _build_dataset(self, run_id: int, rt: Dict[str, Any]) -> Dict[str, Any]:
        run_dir = RUNS_ROOT / f"run_{run_id}"
        ds_dir = run_dir / "dataset"
        ds_dir.mkdir(parents=True, exist_ok=True)
        train_path = ds_dir / "train.jsonl"
        valid_path = ds_dir / "valid.jsonl"
        rag_path = ds_dir / "rag_corpus.jsonl"
        summary_csv = ds_dir / "dataset_summary.csv"

        files = list(self._iter_files(rt))
        chunk_chars = _safe_int(rt.get("chunk_chars"), 2400)
        overlap = _safe_int(rt.get("chunk_overlap"), 220)
        max_chunks = _safe_int(rt.get("max_chunks"), 22000)
        valid_pct = float(rt.get("valid_split", 0.06))

        total_chunks = 0
        train_n = 0
        valid_n = 0
        bytes_total = 0

        with train_path.open("w", encoding="utf-8") as f_train, valid_path.open("w", encoding="utf-8") as f_valid, rag_path.open(
            "w", encoding="utf-8"
        ) as f_rag:
            for p in files:
                rel = str(p.resolve()).replace(str(ROOT.resolve()), "").lstrip("/")
                content = self._read_text(p)
                if not content.strip():
                    continue
                bytes_total += len(content.encode("utf-8", errors="ignore"))
                chunks = self._chunks(content, chunk_chars, overlap)
                for idx, ch in enumerate(chunks):
                    if total_chunks >= max_chunks:
                        break
                    chunk_id = hashlib.sha1(f"{rel}:{idx}:{ch[:120]}".encode("utf-8")).hexdigest()
                    prompt = (
                        f"Nexus internal reference ({rel}). Keep the operational details precise and actionable.\n"
                        f"Focus on workflows, APIs, safety gates, and implementation behavior."
                    )
                    rec = {
                        "messages": [
                            {"role": "system", "content": "You are Nexus Brain, the internal operator model for Nexus."},
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": ch},
                        ],
                        "meta": {"id": chunk_id, "path": rel, "chunk_index": idx},
                    }
                    line = json.dumps(rec, ensure_ascii=False)
                    split_seed = int(hashlib.sha1(f"{rel}:{idx}".encode("utf-8")).hexdigest()[:8], 16) % 1000
                    is_valid = split_seed < int(valid_pct * 1000)
                    if is_valid:
                        f_valid.write(line + "\n")
                        valid_n += 1
                    else:
                        f_train.write(line + "\n")
                        train_n += 1
                    rag = {"id": chunk_id, "path": rel, "chunk_index": idx, "content": ch}
                    f_rag.write(json.dumps(rag, ensure_ascii=False) + "\n")
                    total_chunks += 1
                if total_chunks >= max_chunks:
                    break

        with summary_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value"])
            w.writerow(["source_files", len(files)])
            w.writerow(["bytes_total", bytes_total])
            w.writerow(["chunks_total", total_chunks])
            w.writerow(["train_examples", train_n])
            w.writerow(["valid_examples", valid_n])
            w.writerow(["chunk_chars", chunk_chars])
            w.writerow(["chunk_overlap", overlap])
            w.writerow(["valid_split", valid_pct])

        self._artifact(run_id, "dataset_train_jsonl", train_path, {"rows": train_n})
        self._artifact(run_id, "dataset_valid_jsonl", valid_path, {"rows": valid_n})
        self._artifact(run_id, "rag_corpus_jsonl", rag_path, {"rows": total_chunks})
        self._artifact(run_id, "dataset_summary_csv", summary_csv, {"rows": 9})

        return {
            "train_jsonl": train_path,
            "valid_jsonl": valid_path,
            "rag_jsonl": rag_path,
            "stats": {
                "source_files": len(files),
                "bytes_total": bytes_total,
                "chunks_total": total_chunks,
                "train_examples": train_n,
                "valid_examples": valid_n,
            },
        }

    def _write_runpod_pack(self, run_id: int, rt: Dict[str, Any], dataset: Dict[str, Any]) -> Dict[str, Any]:
        run_dir = RUNS_ROOT / f"run_{run_id}"
        pack = run_dir / "runpod_pack"
        pack.mkdir(parents=True, exist_ok=True)

        train_jsonl = dataset["train_jsonl"]
        valid_jsonl = dataset["valid_jsonl"]
        rag_jsonl = dataset["rag_jsonl"]

        # Lightweight training entrypoint template for Runpod pods.
        train_py = pack / "train_unsloth.py"
        train_py.write_text(
            (
                "from datasets import load_dataset\n"
                "from trl import SFTTrainer\n"
                "from transformers import AutoTokenizer, AutoModelForCausalLM, TrainingArguments\n"
                "from peft import LoraConfig, get_peft_model\n"
                "\n"
                f"BASE_MODEL = {json.dumps(rt.get('base_model'))}\n"
                f"TRAIN_FILE = {json.dumps(str(train_jsonl))}\n"
                f"VALID_FILE = {json.dumps(str(valid_jsonl))}\n"
                "OUT_DIR = './nexus_brain_lora'\n"
                "\n"
                "def to_text(example):\n"
                "    msgs = example['messages']\n"
                "    parts = []\n"
                "    for m in msgs:\n"
                "        parts.append(f\"<{m['role']}>\\n{m['content']}\")\n"
                "    return {'text': '\\n\\n'.join(parts)}\n"
                "\n"
                "dataset = load_dataset('json', data_files={'train': TRAIN_FILE, 'validation': VALID_FILE})\n"
                "dataset = dataset.map(to_text)\n"
                "tok = AutoTokenizer.from_pretrained(BASE_MODEL, use_fast=True)\n"
                "tok.pad_token = tok.eos_token\n"
                "model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype='auto', device_map='auto')\n"
                "lora_cfg = LoraConfig(\n"
                f"    r={_safe_int(rt.get('lora_rank'), 16)},\n"
                f"    lora_alpha={_safe_int(rt.get('lora_alpha'), 32)},\n"
                f"    lora_dropout={_safe_float(rt.get('lora_dropout'), 0.05)},\n"
                "    bias='none',\n"
                "    task_type='CAUSAL_LM',\n"
                ")\n"
                "model = get_peft_model(model, lora_cfg)\n"
                "args = TrainingArguments(\n"
                "    output_dir=OUT_DIR,\n"
                f"    num_train_epochs={_safe_int(rt.get('epochs'), 2)},\n"
                f"    per_device_train_batch_size={_safe_int(rt.get('micro_batch_size'), 2)},\n"
                f"    gradient_accumulation_steps={_safe_int(rt.get('gradient_accumulation_steps'), 8)},\n"
                f"    learning_rate={_safe_float(rt.get('learning_rate'), 2e-4)},\n"
                "    logging_steps=10,\n"
                "    save_steps=200,\n"
                "    eval_steps=200,\n"
                "    evaluation_strategy='steps',\n"
                "    save_total_limit=2,\n"
                "    bf16=True,\n"
                "    fp16=False,\n"
                ")\n"
                "trainer = SFTTrainer(model=model, args=args, train_dataset=dataset['train'], eval_dataset=dataset['validation'], tokenizer=tok, dataset_text_field='text', max_seq_length=2048)\n"
                "trainer.train()\n"
                "trainer.save_model(OUT_DIR)\n"
            ),
            encoding="utf-8",
        )

        req = pack / "requirements.txt"
        req.write_text(
            "\n".join(
                [
                    "torch",
                    "transformers>=4.41.0",
                    "datasets>=2.19.0",
                    "trl>=0.9.6",
                    "peft>=0.11.1",
                    "accelerate>=0.33.0",
                    "bitsandbytes>=0.43.1",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        launch = pack / "launch.sh"
        launch.write_text(
            (
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "python3 -m pip install --upgrade pip\n"
                "python3 -m pip install -r requirements.txt\n"
                "python3 train_unsloth.py\n"
            ),
            encoding="utf-8",
        )
        try:
            launch.chmod(0o755)
        except Exception:
            pass

        manifest = {
            "run_id": run_id,
            "created_at": _now(),
            "root": str(ROOT),
            "dataset": {
                "train_jsonl": str(train_jsonl),
                "valid_jsonl": str(valid_jsonl),
                "rag_jsonl": str(rag_jsonl),
            },
            "training": {
                "base_model": rt.get("base_model"),
                "lora_rank": rt.get("lora_rank"),
                "lora_alpha": rt.get("lora_alpha"),
                "lora_dropout": rt.get("lora_dropout"),
                "epochs": rt.get("epochs"),
                "learning_rate": rt.get("learning_rate"),
                "micro_batch_size": rt.get("micro_batch_size"),
                "gradient_accumulation_steps": rt.get("gradient_accumulation_steps"),
            },
            "pack_files": {
                "train_unsloth_py": str(train_py),
                "requirements_txt": str(req),
                "launch_sh": str(launch),
            },
        }
        manifest_path = pack / "nexus_brain_manifest.json"
        manifest_path.write_text(_json(manifest), encoding="utf-8")
        readme = pack / "README.md"
        readme.write_text(
            (
                "# Nexus Brain Runpod Pack\n\n"
                "This pack was generated by Nexus to train a LoRA adapter for internal workflows.\n\n"
                "## Steps on Runpod\n"
                "1. Upload this `runpod_pack/` directory to your pod.\n"
                "2. Run `bash launch.sh`.\n"
                "3. After training, export `nexus_brain_lora/` and register it in Nexus model routing.\n\n"
                "## Safety\n"
                "- This pack only uses local Nexus files already present in dataset JSONL.\n"
                "- It does not contain outbound automations or social-login actions.\n"
            ),
            encoding="utf-8",
        )

        self._artifact(run_id, "runpod_pack_manifest_json", manifest_path, {"training": manifest["training"]})
        self._artifact(run_id, "runpod_pack_readme_md", readme, {})
        self._artifact(run_id, "runpod_pack_launch_sh", launch, {})
        return {"pack_dir": pack, "manifest_json": manifest_path, "train_py": train_py, "launch_sh": launch}

    def status(self, run_id: int = 0, include_events: bool = True) -> Dict[str, Any]:
        conn = self._conn()
        try:
            if run_id > 0:
                run = conn.execute("SELECT * FROM nexus_brain_runs WHERE id=?", (int(run_id),)).fetchone()
            else:
                run = conn.execute("SELECT * FROM nexus_brain_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not run:
                return {"ok": True, "status": "idle", "run": None, "events": [], "artifacts": []}
            rid = int(run["id"])
            events = []
            if include_events:
                rows = conn.execute(
                    "SELECT id, level, phase, message, payload_json, created_at FROM nexus_brain_events WHERE run_id=? ORDER BY id DESC LIMIT 300",
                    (rid,),
                ).fetchall()
                for r in rows:
                    events.append(
                        {
                            "id": int(r["id"]),
                            "level": r["level"],
                            "phase": r["phase"],
                            "message": r["message"],
                            "payload": json.loads(r["payload_json"] or "{}"),
                            "created_at": r["created_at"],
                        }
                    )
            art = []
            rows = conn.execute(
                "SELECT artifact_type,file_path,meta_json,created_at FROM nexus_brain_artifacts WHERE run_id=? ORDER BY id DESC LIMIT 300",
                (rid,),
            ).fetchall()
            for r in rows:
                art.append(
                    {
                        "artifact_type": r["artifact_type"],
                        "file_path": r["file_path"],
                        "meta": json.loads(r["meta_json"] or "{}"),
                        "created_at": r["created_at"],
                    }
                )
            return {
                "ok": True,
                "status": run["status"],
                "run": {
                    "id": rid,
                    "status": run["status"],
                    "phase": run["phase"],
                    "started_at": run["started_at"],
                    "finished_at": run["finished_at"],
                    "runtime": json.loads(run["runtime_json"] or "{}"),
                    "outputs": json.loads(run["outputs_json"] or "{}"),
                    "error": run["error"] or "",
                },
                "events": events,
                "artifacts": art,
            }
        finally:
            conn.close()

    def list_runs(self, limit: int = 20) -> Dict[str, Any]:
        limit = max(1, min(200, int(limit)))
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id,status,phase,started_at,finished_at,error FROM nexus_brain_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return {
                "ok": True,
                "runs": [
                    {
                        "id": int(r["id"]),
                        "status": r["status"],
                        "phase": r["phase"],
                        "started_at": r["started_at"],
                        "finished_at": r["finished_at"],
                        "error": r["error"] or "",
                    }
                    for r in rows
                ],
            }
        finally:
            conn.close()

    # DB helpers
    def _create_run(self, runtime: Dict[str, Any]) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO nexus_brain_runs (status, phase, runtime_json, outputs_json, started_at) VALUES (?,?,?,?,?)",
                ("running", "plan", _json(runtime), "{}", _now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _set_phase(self, run_id: int, phase: str) -> None:
        conn = self._conn()
        try:
            conn.execute("UPDATE nexus_brain_runs SET phase=? WHERE id=?", (_safe_str(phase), int(run_id)))
            conn.commit()
        finally:
            conn.close()

    def _phase(self, run_id: int) -> str:
        conn = self._conn()
        try:
            row = conn.execute("SELECT phase FROM nexus_brain_runs WHERE id=?", (int(run_id),)).fetchone()
            return _safe_str((row["phase"] if row else ""))
        finally:
            conn.close()

    def _finish_run(self, run_id: int, status: str, outputs: Dict[str, Any], error: str = "") -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE nexus_brain_runs SET status=?, outputs_json=?, error=?, finished_at=? WHERE id=?",
                (_safe_str(status), _json(outputs), _safe_str(error), _now(), int(run_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def _event(self, run_id: int, level: str, phase: str, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO nexus_brain_events (run_id, level, phase, message, payload_json, created_at) VALUES (?,?,?,?,?,?)",
                (int(run_id), _safe_str(level), _safe_str(phase), _safe_str(message), _json(payload or {}), _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _artifact(self, run_id: int, artifact_type: str, file_path: Path, meta: Optional[Dict[str, Any]] = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO nexus_brain_artifacts (run_id, artifact_type, file_path, meta_json, created_at) VALUES (?,?,?,?,?)",
                (int(run_id), _safe_str(artifact_type), str(file_path), _json(meta or {}), _now()),
            )
            conn.commit()
        finally:
            conn.close()


_NEXUS_BRAIN: Optional[NexusBrainPipeline] = None


def get_nexus_brain_pipeline() -> NexusBrainPipeline:
    global _NEXUS_BRAIN
    if _NEXUS_BRAIN is None:
        _NEXUS_BRAIN = NexusBrainPipeline()
    return _NEXUS_BRAIN
