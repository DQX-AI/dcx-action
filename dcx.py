#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

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

def gh_release_asset_url(owner, repo, tag, pattern, token):
    api = f"https://api.github.com/repos/{owner}/{repo}/releases"
    api = f"{api}/latest" if tag == "latest" else f"{api}/tags/{tag}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.loads(http_get(api, headers=headers))
    urls = [a["browser_download_url"] for a in data.get("assets", [])]
    rx = re.compile(pattern)
    for u in urls:
        if rx.search(u):
            return u
    raise RuntimeError(f"No asset matched pattern: {pattern}\nAvailable:\n" + "\n".join(urls))

def uv_tool_install_from_url(url, token):
    # try direct install; fallback to download then install
    try:
        sh(f'uv tool install "{url}" --force', check=True)
        return
    except Exception:
        pass
    tmp = tempfile.mkdtemp(prefix="dcx_")
    try:
        fn = os.path.join(tmp, "pkg")
        headers = {}
        if token and "github.com" in url:
            headers["Authorization"] = f"Bearer {token}"
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
    owner = env("DCX_OWNER", "DQX-AI")
    repo = env("DCX_REPO", "dcx-classification-agent")
    tag = env("DCX_TAG", "latest")
    pattern = env("DCX_ASSET_PATTERN", r"dcx(-|_)\d+\.\d+\.\d+.*\.(whl|tar\.gz)")
    repo_path = env("REPO_PATH", os.getcwd())
    scanner_dir = env("SCANNER_DIR", ".")
    max_checks = env("MAX_CHECKS", "30")
    delay = env("DELAY", "1.0")
    os.environ["AI_ENDPOINT"] = env("AI_ENDPOINT")
    os.environ["AI_API_KEY"] = env("AI_API_KEY")
    token = env("GITHUB_TOKEN")

    # Deps
    ensure_tool("uv", install_uv)

    # Resolve asset URL
    if not dcx_url:
        dcx_url = gh_release_asset_url(owner, repo, tag, pattern, token)
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
