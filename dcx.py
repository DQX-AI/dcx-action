#!/usr/bin/env python3
import os, shutil, subprocess, tempfile, time, urllib.request

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

def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

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

def uv_tool_install_from_url(url, token):
    # Always download the archive locally, then install from the file
    tmp = tempfile.mkdtemp(prefix="dcx_")
    try:
        # Save to a filename with extension so installers detect format
        fn = os.path.join(tmp, "package.tar.gz")
        headers = {}
        if token:
            # Backend expects header name "token: Bearer <token>"
            headers["token"] = f"Bearer {token}"
        with open(fn, "wb") as f:
            f.write(http_get(url, headers=headers))
        sh(f'uv tool install "{fn}" --force', check=True)
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
    token = env("DMC_SERVICE_TOKEN", env("GITHUB_TOKEN"))

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
