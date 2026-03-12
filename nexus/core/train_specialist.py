import inspect
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainerCallback,
    TrainingArguments,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _query_gpu_stats() -> dict:
    if not torch.cuda.is_available():
        return {"gpu_utilization": None, "memory_used_mib": None, "memory_total_mib": None}
    try:
        cmd = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
        first = (out.splitlines() or [""])[0]
        parts = [p.strip() for p in first.split(",")]
        if len(parts) >= 3:
            return {
                "gpu_utilization": int(float(parts[0])),
                "memory_used_mib": int(float(parts[1])),
                "memory_total_mib": int(float(parts[2])),
            }
    except Exception:
        pass
    return {"gpu_utilization": None, "memory_used_mib": None, "memory_total_mib": None}


def _append_json_entry(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except Exception:
            existing = []
    existing.append(entry)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


class GPUUtilizationCallback(TrainerCallback):
    def __init__(self, specialist: str, log_path: Path, every_steps: int = 100) -> None:
        self.specialist = specialist
        self.log_path = log_path
        self.every_steps = max(1, int(every_steps))
        self._last_logged_step = -1

    def on_step_end(self, args, state, control, **kwargs):
        step = int(getattr(state, "global_step", 0) or 0)
        if step <= 0 or step == self._last_logged_step or step % self.every_steps != 0:
            return
        self._last_logged_step = step
        stats = _query_gpu_stats()
        _append_json_entry(
            self.log_path,
            {
                "timestamp": _utc_now_iso(),
                "specialist": self.specialist,
                "step": step,
                "gpu_utilization": stats.get("gpu_utilization"),
                "memory_used_mib": stats.get("memory_used_mib"),
                "memory_total_mib": stats.get("memory_total_mib"),
            },
        )

def _fmt_text(example):
    msgs = example.get('messages')
    if isinstance(msgs, list) and msgs:
        parts = []
        for m in msgs:
            role = str(m.get('role', 'user'))
            content = str(m.get('content', ''))
            parts.append(f'<{role}>\n{content}')
        return {'text': '\n\n'.join(parts)}
    prompt = str(example.get('prompt', ''))
    completion = str(example.get('completion', ''))
    return {'text': (prompt + '\n\n' + completion).strip()}

def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: python train_specialist.py <config.json> [--feedback-data <path>]')
    cfg_path = Path(sys.argv[1]).resolve()
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    if '--feedback-data' in sys.argv:
        try:
            ix = sys.argv.index('--feedback-data')
            if ix + 1 < len(sys.argv):
                cfg['feedback_data_path'] = sys.argv[ix + 1]
        except Exception:
            pass
    os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True')

    specialist = cfg.get('specialist', 'specialist')
    base_model = cfg.get('base_model')
    dataset_path = (cfg_path.parent / cfg.get('dataset_path', '')).resolve()
    output_dir = (cfg_path.parent / cfg.get('output_dir', f'./exports/{specialist}')).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not dataset_path.exists():
        raise SystemExit(f'dataset not found: {dataset_path}')

    print('[RunpodLab] Specialist:', specialist)
    print('[RunpodLab] Base model:', base_model)
    print('[RunpodLab] Dataset:', dataset_path)
    print('[RunpodLab] Output dir:', output_dir)

    h100_profile = cfg.get("h100_profile") or {}
    max_seq_len = int(h100_profile.get('max_seq_len', 2048))
    gradient_checkpointing = bool(h100_profile.get('gradient_checkpointing', True))
    dataloader_num_workers = int(cfg.get("dataloader_num_workers", h100_profile.get("dataloader_num_workers", 4)) or 4)
    dataloader_pin_memory = bool(cfg.get("dataloader_pin_memory", h100_profile.get("dataloader_pin_memory", True)))
    dataloader_prefetch_factor = int(cfg.get("dataloader_prefetch_factor", h100_profile.get("dataloader_prefetch_factor", 2)) or 2)
    enable_torch_compile = bool(cfg.get("torch_compile", True))
    lora = cfg.get('lora') or {}
    micro_batch = int(lora.get('micro_batch_size', 2))
    grad_accum = int(lora.get('gradient_accumulation_steps', 64))
    epochs = int(lora.get('epochs', 2))
    lr = float(lora.get('learning_rate', 2e-4))

    dataset = load_dataset('json', data_files={'train': str(dataset_path)})
    dataset = dataset.map(_fmt_text)
    base_train = dataset['train']
    feedback_path = str(cfg.get('feedback_data_path') or '').strip()
    feedback_weight = float(cfg.get('feedback_weight', 0.30) or 0.30)
    if feedback_path:
        fb_p = Path(feedback_path).resolve()
        if fb_p.exists():
            fb_ds = load_dataset('json', data_files={'train': str(fb_p)})
            fb_ds = fb_ds.map(_fmt_text)
            fb_train = fb_ds['train']
            base_n = len(base_train)
            fb_n = len(fb_train)
            if fb_n > 0 and base_n > 0 and feedback_weight > 0:
                target_fb = int((feedback_weight / max(1e-6, 1.0 - feedback_weight)) * base_n)
                repeats = max(1, int(target_fb / max(1, fb_n)))
                cap_repeats = min(repeats, 20)
                chunks = [base_train]
                for _ in range(cap_repeats):
                    chunks.append(fb_train)
                from datasets import concatenate_datasets
                base_train = concatenate_datasets(chunks)
                print(f'[RunpodLab] Mixed feedback dataset: base={base_n} feedback={fb_n} repeats={cap_repeats} total={len(base_train)}')
        else:
            print(f'[RunpodLab] feedback_data_path not found: {fb_p}')

    tokenizer = AutoTokenizer.from_pretrained(base_model, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = 'right'

    bf16_requested = bool(cfg.get("bf16", True))
    use_bf16 = bf16_requested and torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    torch_dtype = torch.bfloat16 if use_bf16 else torch.float16
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch_dtype,
        trust_remote_code=True,
    )
    model.config.use_cache = False
    if gradient_checkpointing:
        model.gradient_checkpointing_enable()
    if torch.cuda.is_available():
        model = model.to('cuda')

    lora_cfg = LoraConfig(
        r=int(lora.get('rank', 16)),
        lora_alpha=int(lora.get('alpha', 32)),
        lora_dropout=float(lora.get('dropout', 0.05)),
        bias='none',
        task_type='CAUSAL_LM',
        target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],
    )
    model = get_peft_model(model, lora_cfg)
    if enable_torch_compile and hasattr(torch, "compile"):
        try:
            t_compile_start = time.time()
            model = torch.compile(model)
            print(f"[RunpodLab] torch.compile enabled in {time.time() - t_compile_start:.2f}s")
        except Exception as e:
            print(f"[RunpodLab] torch.compile skipped: {e}")

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    if trainable <= 0:
        raise RuntimeError('No trainable parameters detected after LoRA injection')
    print(f'[RunpodLab] Trainable params: {trainable}')

    def _tok(ex):
        t = tokenizer(
            ex['text'],
            truncation=True,
            max_length=max_seq_len,
            padding='max_length',
        )
        t['labels'] = t['input_ids'].copy()
        return t

    train_ds = base_train.map(_tok, remove_columns=base_train.column_names)
    collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    args_kwargs = {
        "output_dir": str(output_dir),
        "num_train_epochs": epochs,
        "per_device_train_batch_size": micro_batch,
        "gradient_accumulation_steps": grad_accum,
        "learning_rate": lr,
        "logging_steps": 10,
        "save_steps": 100,
        "save_total_limit": 2,
        "bf16": use_bf16,
        "fp16": False if use_bf16 else True,
        "report_to": "none",
        "gradient_checkpointing": gradient_checkpointing,
        "dataloader_num_workers": dataloader_num_workers,
        "dataloader_pin_memory": dataloader_pin_memory,
    }
    if dataloader_num_workers > 0:
        args_kwargs["dataloader_prefetch_factor"] = dataloader_prefetch_factor

    allowed_training_args = set(inspect.signature(TrainingArguments.__init__).parameters)
    filtered_args = {k: v for k, v in args_kwargs.items() if k in allowed_training_args}
    args = TrainingArguments(**filtered_args)

    util_log_path = Path(cfg.get("gpu_utilization_log") or (cfg_path.parents[3] / "gpu_utilization_log.json"))

    trainer_kwargs = {
        "model": model,
        "args": args,
        "train_dataset": train_ds,
        "data_collator": collator,
    }
    trainer_sig = inspect.signature(Trainer.__init__).parameters
    if "tokenizer" in trainer_sig:
        trainer_kwargs["tokenizer"] = tokenizer
    elif "processing_class" in trainer_sig:
        trainer_kwargs["processing_class"] = tokenizer

    trainer = Trainer(
        **trainer_kwargs,
        callbacks=[GPUUtilizationCallback(specialist=specialist, log_path=util_log_path, every_steps=100)],
    )
    train_result = trainer.train()
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    metrics = dict(train_result.metrics or {})
    metrics.update({'specialist': specialist, 'train_examples': len(train_ds), 'base_model': base_model, 'trainable_params': int(trainable)})
    (output_dir / 'training_metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[RunpodLab] Training complete:', specialist)

if __name__ == '__main__':
    main()
