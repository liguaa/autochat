"""Download the pinned NanoJev release through the configured HTTP(S) proxy.

The large weight file is fetched in resumable ranges because single long
downloads may stall on some proxy connections. SHA-256 is verified.
"""

from __future__ import annotations

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "checkpoints" / "NanoJev-unified"
REPO = "C-Tianyu/NanoJev"
REVISION = "047b927b30882a1138fc504821b82ac145a4b81a"
WEIGHT_SIZE = 2_385_039_280
WEIGHT_SHA256 = "f68c47d66998231b86b7e91b4ed5e82ae23acf104c8b7cd6d165c3ac7b7ffe1b"
CHUNK_SIZE = 32 * 1024 * 1024
URL = f"https://huggingface.co/{REPO}/resolve/{REVISION}/best.safetensors"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fetch_chunk(index: int, parts: Path) -> Path:
    start = index * CHUNK_SIZE
    end = min(start + CHUNK_SIZE, WEIGHT_SIZE) - 1
    target = parts / f"{index:04d}.part"
    expected = end - start + 1
    if target.exists() and target.stat().st_size == expected:
        return target
    for attempt in range(3):
        try:
            with httpx.stream("GET", URL, headers={"Range": f"bytes={start}-{end}"},
                              follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                if response.status_code != 206 or response.headers.get("content-range") != f"bytes {start}-{end}/{WEIGHT_SIZE}":
                    raise RuntimeError(f"Unexpected range response for chunk {index}")
                temporary = target.with_suffix(".tmp")
                with temporary.open("wb") as output:
                    for block in response.iter_bytes(1024 * 1024):
                        output.write(block)
                if temporary.stat().st_size != expected:
                    raise RuntimeError(f"Incomplete chunk {index}")
                temporary.replace(target)
                return target
        except (httpx.HTTPError, OSError, RuntimeError):
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPO,
        revision=REVISION,
        local_dir=DEST,
        allow_patterns=["config.json", "tokenizer/*", "backbone_config/*"],
    )
    weights = DEST / "best.safetensors"
    if weights.is_file() and weights.stat().st_size == WEIGHT_SIZE and sha256_file(weights) == WEIGHT_SHA256:
        print(f"Already verified: {weights}")
        return
    parts = DEST / ".weight_parts"
    parts.mkdir(exist_ok=True)
    count = math.ceil(WEIGHT_SIZE / CHUNK_SIZE)
    print(f"Downloading {count} ranges through configured proxy; existing ranges will be reused.", flush=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_chunk, index, parts) for index in range(count)]
        for done, future in enumerate(as_completed(futures), start=1):
            future.result()
            if done % 8 == 0 or done == count:
                print(f"{done}/{count} ranges complete", flush=True)
    temporary_weights = weights.with_suffix(".assembling")
    with temporary_weights.open("wb") as output:
        for index in range(count):
            with (parts / f"{index:04d}.part").open("rb") as source:
                while block := source.read(8 * 1024 * 1024):
                    output.write(block)
    if temporary_weights.stat().st_size != WEIGHT_SIZE or sha256_file(temporary_weights) != WEIGHT_SHA256:
        temporary_weights.unlink(missing_ok=True)
        raise RuntimeError("Weight file SHA-256 mismatch")
    temporary_weights.replace(weights)
    for index in range(count):
        (parts / f"{index:04d}.part").unlink()
    parts.rmdir()
    print(f"Verified: {weights}")


if __name__ == "__main__":
    main()
