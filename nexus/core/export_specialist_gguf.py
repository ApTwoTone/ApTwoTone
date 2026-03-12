#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Tuple


MIN_VALID_GGUF_BYTES = 5 * 1024 * 1024


def _run(cmd, cwd: Path | None = None) -> Tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    return p.returncode, out.strip()


def _valid_file(path: Path, min_bytes: int = MIN_VALID_GGUF_BYTES) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size >= min_bytes
    except Exception:
        return False


def _locate_llama_tools() -> Dict[str, Path]:
    tools: Dict[str, Path] = {}
    convert = shutil.which("convert_hf_to_gguf.py")
    quantize = shutil.which("llama-quantize") or shutil.which("quantize")
    if convert:
        tools["convert"] = Path(convert)
    if quantize:
        tools["quantize"] = Path(quantize)
    # Common local layout fallback
    if "convert" not in tools:
        p = Path("/workspace/llama.cpp/convert_hf_to_gguf.py")
        if p.exists():
            tools["convert"] = p
    if "quantize" not in tools:
        for p in (Path("/workspace/llama.cpp/llama-quantize"), Path("/workspace/llama.cpp/quantize")):
            if p.exists():
                tools["quantize"] = p
                break
    return tools


def _load_cfg(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def export_from_config(cfg_path: Path) -> Dict:
    cfg = _load_cfg(cfg_path)
    specialist = str(cfg.get("specialist", "")).strip() or cfg_path.stem
    base_model = str(cfg.get("base_model", "")).strip()
    output_dir = (cfg_path.parent / str(cfg.get("output_dir", ""))).resolve()
    adapter_file = output_dir / "adapter_model.safetensors"
    metrics_file = output_dir / "training_metrics.json"

    if not adapter_file.exists():
        return {"ok": False, "specialist": specialist, "error": f"missing adapter: {adapter_file}"}
    if not base_model:
        return {"ok": False, "specialist": specialist, "error": "missing base_model in config"}

    tools = _locate_llama_tools()
    if "convert" not in tools or "quantize" not in tools:
        return {
            "ok": False,
            "specialist": specialist,
            "error": "llama.cpp tools not found (convert_hf_to_gguf.py + llama-quantize required)",
        }

    gguf_root = output_dir.parents[2] / "exports" / "gguf" / specialist
    gguf_root.mkdir(parents=True, exist_ok=True)
    q4 = gguf_root / f"{specialist}-q4_k_m.gguf"
    q5 = gguf_root / f"{specialist}-q5_k_m.gguf"

    # Skip if already valid.
    if _valid_file(q4) and _valid_file(q5):
        return {"ok": True, "specialist": specialist, "q4": str(q4), "q5": str(q5), "cached": True}

    with tempfile.TemporaryDirectory(prefix=f"{specialist}_merged_") as td:
        merged_dir = Path(td) / "merged"
        merged_dir.mkdir(parents=True, exist_ok=True)

        merge_py = [
            "python3",
            "-c",
            (
                "from peft import PeftModel; "
                "from transformers import AutoModelForCausalLM, AutoTokenizer; "
                f"base='{base_model}'; adapter=r'{str(output_dir)}'; out=r'{str(merged_dir)}'; "
                "m=AutoModelForCausalLM.from_pretrained(base, trust_remote_code=True); "
                "pm=PeftModel.from_pretrained(m, adapter); "
                "mm=pm.merge_and_unload(); "
                "mm.save_pretrained(out); "
                "tok=AutoTokenizer.from_pretrained(base, use_fast=True); "
                "tok.save_pretrained(out)"
            ),
        ]
        code, out = _run(merge_py)
        if code != 0:
            return {"ok": False, "specialist": specialist, "error": f"merge_failed: {out[:800]}"}

        fp16 = gguf_root / f"{specialist}-fp16.gguf"
        convert_cmd = ["python3", str(tools["convert"]), str(merged_dir), "--outfile", str(fp16), "--outtype", "f16"]
        code, out = _run(convert_cmd)
        if code != 0 or not _valid_file(fp16, min_bytes=1_000_000):
            return {"ok": False, "specialist": specialist, "error": f"convert_failed: {out[:800]}"}

        code, out = _run([str(tools["quantize"]), str(fp16), str(q4), "Q4_K_M"])
        if code != 0:
            return {"ok": False, "specialist": specialist, "error": f"q4_quantize_failed: {out[:800]}"}
        code, out = _run([str(tools["quantize"]), str(fp16), str(q5), "Q5_K_M"])
        if code != 0:
            return {"ok": False, "specialist": specialist, "error": f"q5_quantize_failed: {out[:800]}"}

    if not _valid_file(q4) or not _valid_file(q5):
        return {"ok": False, "specialist": specialist, "error": "invalid_quantized_files"}

    summary = {
        "ok": True,
        "specialist": specialist,
        "q4": str(q4),
        "q5": str(q5),
        "adapter": str(adapter_file),
        "metrics_exists": metrics_file.exists(),
    }
    (gguf_root / "export_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Export specialist adapter to GGUF")
    parser.add_argument("--config", required=True, help="Path to specialist training config json")
    args = parser.parse_args()
    result = export_from_config(Path(args.config).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
