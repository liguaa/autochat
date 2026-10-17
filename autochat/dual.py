"""Run NanoJev analysis, then DeepSeek candidate generation for one conversation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .cli import build_request, context_version, load_nanojev_analysis, main as reply_main, read_conversation
from .credentials import get_api_key


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTORCH_PYTHON = Path(os.environ.get(
    "AUTOCHAT_PYTORCH_PYTHON",
    r"D:\deepstudy\Miniconda\envs\pytorch_env\python.exe",
))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--checkpoint-dir", type=Path, default=ROOT / "checkpoints" / "NanoJev-unified")
    parser.add_argument("--pytorch-python", type=Path, default=DEFAULT_PYTORCH_PYTHON)
    parser.add_argument("--dry-run", action="store_true", help="运行 NanoJev 并检查 DeepSeek 请求，不发起 API 调用")
    args = parser.parse_args(argv)
    try:
        data = read_conversation(args.input)
        version = context_version(data)
        if not args.pytorch_python.is_file():
            raise FileNotFoundError(f"找不到 CUDA Python: {args.pytorch_python}")
        if not (args.checkpoint_dir / "best.safetensors").is_file():
            raise FileNotFoundError(f"找不到 NanoJev 权重: {args.checkpoint_dir}")
        if not args.dry_run and not get_api_key():
            raise RuntimeError("请先配置 DeepSeek API 密钥；可先用 --dry-run 验证双模型路径")
        analysis_path = ROOT / "results" / f"nanojev_chat_{version}.json"
        command = [
            str(args.pytorch_python),
            str(ROOT / "scripts" / "run_nanojev_with_pytorch_env.py"),
            "--chat-analysis",
            "--input", str(args.input.resolve()),
            "--checkpoint-dir", str(args.checkpoint_dir.resolve()),
            "--output", str(analysis_path),
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
        if completed.returncode != 0:
            raise RuntimeError("NanoJev 分析失败：" + completed.stderr[-500:].strip())
        nanojev = load_nanojev_analysis(analysis_path, version)
        if args.dry_run:
            request = build_request(data, nanojev)
            print(json.dumps({
                "context_version": version,
                "pipeline_mode": "dual",
                "small_model": "NanoJev",
                "small_model_chat_domain_validated": nanojev["chat_domain_validated"],
                "large_model": request["model"],
                "api_key_configured": bool(get_api_key()),
                "api_called": False,
                "send_status": "not_sent",
                "analysis_output": str(analysis_path),
            }, ensure_ascii=False))
            return 0
        output = args.output or ROOT / "results" / f"reply_{version}.json"
        return reply_main([
            "--input", str(args.input),
            "--mode", "dual",
            "--nanojev-analysis", str(analysis_path),
            "--output", str(output),
        ])
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
