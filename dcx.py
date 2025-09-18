#!/usr/bin/env python3
import os
import sys
import json
import time
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple

# ------------- utils -------------

def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)

def log(msg: str) -> None:
    print(f"[dcx-action] {msg}", flush=True)

def sh(cmd, check: bool = True, capture: bool = False) -> str:
    res = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if check and res.returncode != 0:
        out = res.stdout or ""
        raise RuntimeError(f"cmd failed ({res.returncode}): {cmd}\n{out}")
    return (res.stdout or "").strip() if capture else ""

def http_get_with_meta(url: str, headers: Optional[dict] = None) -> Tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        content = r.read()
        ctype = r.headers.get("Content-Type", "")
        return content, ctype

def ensure_tool(name: str, install_fn=None) -> None:
    if shutil.which(name):
        return
    if not install_fn:
        raise RuntimeError(f"{name} not found")
    install_fn()
    if not shutil.which(name):
        raise RuntimeError(f"{name} install failed")

def install_uv() -> None:
    # Install uv into ~/.uv/bin and update PATH for current process
    sh('bash -lc "curl -LsSf https://astral.sh/uv/install.sh | sh"', check=True)
    uv_bin = str(Path.home() / ".uv" / "bin")
    os.environ["PATH"] = f"{uv_bin}{os.pathsep}{os.environ.get('PATH','')}"

# ------------- domain -------------

def dcx_service_download_url(tag: str) -> str:
    # Example: https://api.withdmc.com/v1/dmc/github/releases/download?tag=latest
    return f"https://api.withdmc.com/v1/dmc/github/releases/download?tag={tag}"

_URL_ASSET_RX = re.compile(r"https?://[^\s\"']+\.(?:whl|tar\.gz)(?:\?[^\s\"']*)?$", re.I)

def _extract_download_url_from_json(payload: dict) -> str:
    for key in ("download_url", "url", "asset_url", "browser_download_url"):
        val = payload.get(key)
        if isinstance(val, str) and _URL_ASSET_RX.match(val):
            return val

    found = None
    def walk(obj):
        nonlocal found
        if found:
            return
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            if _URL_ASSET_RX.search(obj):
                found = obj
    walk(payload)
    return found or ""

def _write_bytes(fp: Path, data: bytes) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "wb") as f:
        f.write(data)

def install_from_archive(archive_path: Path) -> None:
    strategies = [
        f'uv tool install "{archive_path}" --force',
        f'uv pip install --user "{archive_path}"',
        f'pip3 install --user "{archive_path}"',
    ]
    for i, cmd in enumerate(strategies, 1):
        try:
            log(f"install step {i}/3: {cmd}")
            sh(cmd, check=True)
            return
        except Exception as e:
            log(f"install step {i} failed: {e}")
    raise RuntimeError("dcx install failed via all strategies")

def uv_tool_install_from_url(url: str, token: str) -> None:
    with tempfile.TemporaryDirectory(prefix="dcx_") as tmpd:
        tmp = Path(tmpd)
        headers = {"token": f"Bearer {token}"} if token else {}
        data, ctype = http_get_with_meta(url, headers=headers)

        real_url = url
        if ctype.startswith("application/json") or (data[:1] == b"{" and data[-1:] == b"}"):
            try:
                meta = json.loads(data.decode("utf-8"))
            except Exception:
                meta = {}
            real_url = _extract_download_url_from_json(meta)
            if not real_url:
                raise RuntimeError("service returned JSON without a usable download URL")
            data, _ = http_get_with_meta(real_url, headers={})

        # filename and extension
        path_part = urllib.parse.urlparse(real_url).path
        base = Path(path_part).name or "package.tar.gz"
        if not (base.endswith(".whl") or base.endswith(".tar.gz")):
            base += ".tar.gz"

        archive = tmp / base
        _write_bytes(archive, data)
        log(f"downloaded: {archive} ({archive.stat().st_size} bytes)")
        install_from_archive(archive)

def _dcx_cmd() -> str:
    dcx = shutil.which("dcx")
    if dcx:
        return f'"{dcx}"'
    # Prefer uv-run if available, else python -m
    if shutil.which("uv"):
        return "uv run -m dcx"
    py = shutil.which("python3") or sys.executable
    return f'"{py}" -m dcx'

def _latest_scan_dir() -> Optional[Path]:
    try:
        out = sh('bash -lc "ls -td output/dcx-scan-* 2>/dev/null | head -n 1"', capture=True)
        return Path(out) if out else None
    except Exception:
        return None

def run_makeflow(scanner_dir: Path, repo_path: Path, max_checks: int, delay: float) -> None:
    cwd = Path.cwd()
    try:
        os.chdir(scanner_dir)
        # Scan
        sh(f'make scan-full REPO="{repo_path}"', check=True)
        scan_dir = _latest_scan_dir()
        if not scan_dir:
            raise RuntimeError("no scan directory under output/")
        scan_id = scan_dir.name
        # Poll and combine
        sh(f'make check-ai-results-scan SCAN_ID="{scan_id}"', check=True)
        sh(f'make check-ai-results-verbose MAX_CHECKS="{max_checks}" DELAY="{delay}"', check=True)
        sh(f'make combine-analysis SCAN_ID="{scan_id}"', check=True)
        log(f"done: output/{scan_id}")
    finally:
        os.chdir(cwd)

def run_cli(repo_path: Path, max_checks: int, delay: float) -> None:
    cmd = _dcx_cmd()
    # Scan
    sh(f'{cmd} scan --repo "{repo_path}" --out "output"', check=True)
    # Poll status up to max_checks
    for _ in range(max_checks):
        try:
            sh(f'{cmd} status --out "output"', check=True)
            break
        except Exception:
            time.sleep(delay)
    # Combine (best-effort)
    try:
        sh(f'{cmd} combine --out "output"', check=True)
    except Exception:
        pass
    log("complete")

# ------------- main -------------

def main() -> None:
    # inputs
    dcx_url = env("DCX_URL")
    tag = env("DCX_TAG", "latest")
    repo_path = Path(env("REPO_PATH", os.getcwd()))
    scanner_dir = Path(env("SCANNER_DIR", "."))
    max_checks = int(float(env("MAX_CHECKS", "30")))
    delay = float(env("DELAY", "1.0"))
    os.environ["AI_ENDPOINT"] = env("AI_ENDPOINT")
    os.environ["AI_API_KEY"] = env("AI_API_KEY")
    token = env("DCX_SERVICE_TOKEN", env("GITHUB_TOKEN"))

    # deps
    ensure_tool("uv", install_uv)

    # resolve URL
    if not dcx_url:
        dcx_url = dcx_service_download_url(tag)
    log(f"install from: {dcx_url}")

    # install dcx-cli
    uv_tool_install_from_url(dcx_url, token)

    # run flow
    if (scanner_dir / "Makefile").is_file():
        run_makeflow(scanner_dir, repo_path, max_checks, delay)
    else:
        run_cli(repo_path, max_checks, delay)

if __name__ == "__main__":
    main()
