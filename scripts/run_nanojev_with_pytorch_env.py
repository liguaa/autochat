"""Run the smoke evaluation using the existing pytorch_env CUDA installation.

This local shim preloads a compatible Intel OpenMP DLL because the copy in
pytorch_env currently lacks symbols required by its torch_cpu.dll. It changes
only this process and does not modify either Conda environment.
"""

import ctypes
import os
import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONDA_ENVS = Path(sys.executable).resolve().parents[1]
DEFAULT_LIBIOMP = CONDA_ENVS / "open-webui" / "Lib" / "site-packages" / "torch" / "lib" / "libiomp5md.dll"
LIBIOMP = Path(os.environ.get("AUTOCHAT_LIBIOMP_DLL", str(DEFAULT_LIBIOMP)))
if not LIBIOMP.is_file():
    raise FileNotFoundError(f"Compatible libiomp5md.dll not found: {LIBIOMP}")
ctypes.WinDLL(str(LIBIOMP))
import torch  # noqa: E402

if not torch.cuda.is_available():
    raise RuntimeError("pytorch_env CUDA is unavailable")
DEPS = ROOT / ".venv313" / "Lib" / "site-packages"
if not DEPS.is_dir():
    raise FileNotFoundError(f"Prototype dependencies missing: {DEPS}")
sys.path.append(str(DEPS))
sys.path.insert(0, str(ROOT))
print(f"Using torch {torch.__version__}, CUDA {torch.version.cuda}: {torch.cuda.get_device_name(0)}", flush=True)
tasks = {"--emotion-only": "evaluate_emotion.py", "--chat-analysis": "analyze_chat_nanojev.py"}
selected = [flag for flag in tasks if flag in sys.argv[1:]]
if len(selected) > 1:
    raise ValueError("Select only one NanoJev task")
target = tasks[selected[0]] if selected else "evaluate_nanojev.py"
if selected:
    sys.argv.remove(selected[0])
runpy.run_path(str(ROOT / "scripts" / target), run_name="__main__")
