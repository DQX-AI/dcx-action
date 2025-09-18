#!/usr/bin/env python3
import os, shutil, subprocess, tempfile, time, urllib.request, json, re, urllib.parse

def env(name, default=""):
    return os.environ.get(name, default)

def sh(cmd, check=True, capture=False):
    res = subprocess.run(cmd, shell=isinstance(cmd, str), check=False,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.STDOUT)
    if check and res.returncode != 0:
        out = res.stdout.decode() if res.stdout else ""
        raise RuntimeError(f"cmd failed ({res.returncode}): {cmd}\n{out}")
    return res.stdout.decode().strip() if capture and res.stdout else ""

def http_get_with_meta(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        content = r.read()
        ctype = r.headers.get("Content-Type", "")
        return content, ctype

def http_get(url, headers=None):
    content, _ = http_get_with_meta(url, headers=headers)
    return content

def ensure_tool(name, install_fn=None):
    if shutil.which(name):
        return
    if not install_fn:
        raise RuntimeError(f"{name} not found")
    install_fn()
    if not shutil.which(name):
        raise RuntimeError(f"{name} install failed")

def install_uv():
    # runner has curl; install uv to ~/.uv/bin
    sh('bash -lc "curl -LsSf https://astral.sh/uv/install.sh | sh"', check=True)
    uv_bin = os.path.expanduser("~/.uv/bin")
    os.environ["PATH"] = f"{uv_bin}:" + os.environ["PATH"]

def dcx_service_download_url(tag):
    # Build the DMC service endpoint for dcx asset download
    # Example: http://3.101.151.224:8000/v1/dmc/github/releases/download?tag=latest
    return f"https://api.withdmc.com/v1/dmc/github/releases/download?tag={tag}"

def _extract_download_url_from_json(payload: dict) -> str:
    # Try common keys first
    for key in ["download_url", "url", "asset_url", "browser_download_url"]:
        val = payload.get(key)
        if isinstance(val, str):
            return val
    # Search recursively for plausible asset URLs
    candidate = None
    rx = re.compile(r"https?://[^\s\"]+\.(whl|tar\.gz)(\?[^\s\"]*)?$")
    def walk(obj):
        nonlocal candidate
        if candidate:
            return
        if isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
        elif isinstance(obj, str):
            if rx.search(obj):
                candidate = obj
    walk(payload)
    return candidate or ""

def uv_tool_install_from_url(url, token):
    # Always download the archive locally, then install from the file
    tmp = tempfile.mkdtemp(prefix="dcx_")
    try:
        headers = {}
        if token:
            # Backend expects header name "token: Bearer <token>"
            headers["token"] = f"Bearer {token}"

        # First request – could be JSON metadata or the archive itself
        body, ctype = http_get_with_meta(url, headers=headers)

        # If JSON, extract the real download URL
        if ctype.startswith("application/json") or (body[:1] == b"{" and body[-1:] == b"}"):
            try:
                meta = json.loads(body.decode("utf-8"))
            except Exception:
                meta = {}
            real_url = _extract_download_url_from_json(meta)
            if not real_url:
                raise RuntimeError("Service returned JSON without a usable download URL")
            # Follow-up download likely does not need the service token
            body, ctype = http_get_with_meta(real_url, headers={})
            # Derive a sensible filename from URL path
            path = urllib.parse.urlparse(real_url).path
            base = os.path.basename(path) or "package.tar.gz"
        else:
            # Direct archive response
            base = "package.tar.gz"

        # Ensure proper extension for uv/pip detection
        if not (base.endswith(".whl") or base.endswith(".tar.gz")):
            base = base + ".tar.gz"
        fn = os.path.join(tmp, base)

        with open(fn, "wb") as f:
            f.write(body)
        size = os.path.getsize(fn)
        print(f"[dcx-action] downloaded: {fn} ({size} bytes)")
        # Primary: install as a tool (exposes console scripts)
        try:
            sh(f'uv tool install "{fn}" --force')
            return
        except Exception as e:
            print(f"[dcx-action] uv tool install failed, trying uv pip install...\n{e}")
        # Fallback 1: install into user site via uv pip
        try:
            sh(f'uv pip install --user "{fn}"')
            return
        except Exception as e:
            print(f"[dcx-action] uv pip install failed, trying pip3 install...\n{e}")
        # Fallback 2: system pip3 user install
        sh(f'pip3 install --user "{fn}"', check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def run_makeflow(scanner_dir, repo_path, max_checks, delay):
    os.chdir(scanner_dir)
    os.environ["PYTHON"] = "uv run python" if shutil.which("uv") else "python3"
    sh(f'make scan-full REPO="{repo_path}"')
    scan_dir = sh('bash -lc "ls -td output/dcx-scan-* 2>/dev/null | head -n 1"', capture=True)
    if not scan_dir:
        raise RuntimeError("No scan directory under output/")
    scan_id = os.path.basename(scan_dir)
    sh(f'make check-ai-results-scan SCAN_ID="{scan_id}"')
    sh(f'make check-ai-results-verbose MAX_CHECKS="{max_checks}" DELAY="{delay}"')
    sh(f'make combine-analysis SCAN_ID="{scan_id}"')
    print(f"[dcx-action] done: output/{scan_id}")

def run_cli(repo_path, max_checks, delay):
    dcx = shutil.which("dcx")
    cmd = f'{dcx} ' if dcx else 'uv run -m dcx '
    sh(cmd + f'scan --repo "{repo_path}" --out "output"')
    for _ in range(int(float(max_checks))):
        try:
            sh(cmd + 'status --out "output"')
            break
        except Exception:
            time.sleep(float(delay))
    try:
        sh(cmd + 'combine --out "output"')
    except Exception:
        pass
    print("[dcx-action] complete")

def main():
    # Inputs
    dcx_url = env("DCX_URL")
    # Owner/repo/pattern are no longer needed when using the DMC service endpoint,
    # but keep inputs backward-compatible if DCX_URL is provided explicitly.
    tag = env("DCX_TAG", "latest")
    repo_path = env("REPO_PATH", os.getcwd())
    scanner_dir = env("SCANNER_DIR", ".")
    max_checks = env("MAX_CHECKS", "30")
    delay = env("DELAY", "1.0")
    os.environ["AI_ENDPOINT"] = env("AI_ENDPOINT")
    os.environ["AI_API_KEY"] = env("AI_API_KEY")
    # Prefer dedicated service token if provided; fall back to GITHUB_TOKEN
    token = env("DCX_SERVICE_TOKEN", env("GITHUB_TOKEN"))

    # Deps
    ensure_tool("uv", install_uv)

    # Resolve asset URL (default to DMC service endpoint if not explicitly provided)
    if not dcx_url:
        dcx_url = dcx_service_download_url(tag)
    print(f"[dcx-action] install from: {dcx_url}")

    # Install dcx-cli
    uv_tool_install_from_url(dcx_url, token)

    # Run flow
    if os.path.isfile(os.path.join(scanner_dir, "Makefile")):
        run_makeflow(scanner_dir, repo_path, max_checks, delay)
    else:
        run_cli(repo_path, max_checks, delay)

if __name__ == "__main__":
    main()
