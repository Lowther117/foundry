#!/usr/bin/env python3
"""Foundry - one job: fetch every public repo of a GitHub account into ~/GitHub,
build each app with its own build script, and rebuild only what has changed.

    python foundry.py              sync + build whatever changed or is unbuilt
    python foundry.py --list       show repos and what would happen, change nothing
    python foundry.py --force      rebuild everything
    python foundry.py --only findex --only lectern
    python foundry.py --no-build   sync only (the old Update Repos behaviour)
    python foundry.py --verbose    stream build output to the terminal as well as the log

Needs only Python 3.8+. Uses git when it is installed; otherwise downloads
zipballs and tracks commits itself. Settings live in foundry.json beside this file.
"""
import argparse
import datetime as _dt
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
BUILD_SCRIPT = "build-exe.bat" if IS_WIN else "build-app.command"

DEFAULTS = {
    "owner": "Lowther117",
    "root": "~/GitHub",            # where the repos live
    "skip": [],                    # repo names never cloned or built
    "no_build": [],                # repo names cloned but never built
    "shortcuts": True,             # collect launchers into <root>/_Apps
    "include_forks": False,
    "include_archived": False,
}


# ----------------------------------------------------------------------------- utils
def log(msg=""):
    print(msg, flush=True)


def now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config():
    cfg = dict(DEFAULTS)
    path = HERE / "foundry.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log("foundry.json could not be read (%s) - using defaults" % e)
    return cfg


def run(cmd, cwd=None, capture=True, **kw):
    """Run a command; return CompletedProcess. Never raises on non-zero exit."""
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        stdin=subprocess.DEVNULL, **kw)


def have_git():
    return shutil.which("git") is not None


# ----------------------------------------------------------------------------- GitHub API
def api(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "foundry",
        "Accept": "application/vnd.github+json",
    })
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")), r.headers


def list_repos(cfg):
    repos, page = [], 1
    while True:
        url = "https://api.github.com/users/%s/repos?per_page=100&type=owner&page=%d" % (cfg["owner"], page)
        try:
            data, _ = api(url)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                sys.exit("GitHub API refused (rate limit?). Set GITHUB_TOKEN or wait an hour.")
            raise
        if not data:
            break
        repos.extend(data)
        if len(data) < 100:
            break
        page += 1
    out = []
    for r in repos:
        if r.get("private"):
            continue
        if r.get("fork") and not cfg["include_forks"]:
            continue
        if r.get("archived") and not cfg["include_archived"]:
            continue
        if r["name"] in cfg["skip"]:
            continue
        out.append(r)
    return sorted(out, key=lambda r: r["name"].lower())


def remote_sha(repo):
    """Latest commit on the default branch, via git ls-remote if possible, else the API."""
    branch = repo.get("default_branch") or "main"
    if have_git():
        p = run(["git", "ls-remote", repo["clone_url"], "refs/heads/" + branch])
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.split()[0]
    data, _ = api("https://api.github.com/repos/%s/commits/%s" % (repo["full_name"], branch))
    return data["sha"]


# ----------------------------------------------------------------------------- state
class State:
    def __init__(self, root):
        self.dir = root / ".foundry"
        self.logs = self.dir / "logs"
        self.file = self.dir / "state.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(exist_ok=True)
        self.data = {}
        if self.file.exists():
            try:
                self.data = json.loads(self.file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.data = {}

    def get(self, name):
        return self.data.setdefault(name, {})

    def save(self):
        self.file.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")


# ----------------------------------------------------------------------------- sync
def git_head(d):
    p = run(["git", "rev-parse", "HEAD"], cwd=d)
    return p.stdout.strip() if p.returncode == 0 else None


def sync_git(repo, d):
    """Clone or pull. Returns (ok, changed, sha, message)."""
    if not (d / ".git").exists():
        p = run(["git", "clone", "--quiet", repo["clone_url"], str(d)])
        if p.returncode != 0:
            return False, False, None, "clone failed: " + p.stdout.strip()[-400:]
        return True, True, git_head(d), "cloned"
    before = git_head(d)
    lock = d / ".git" / "index.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 300:
        try:
            lock.unlink()
        except OSError:
            pass
    p = run(["git", "pull", "--rebase", "--autostash", "--quiet"], cwd=d)
    if p.returncode != 0:
        return False, False, before, "pull failed: " + p.stdout.strip()[-400:]
    after = git_head(d)
    return True, before != after, after, ("updated" if before != after else "up to date")


def sync_zip(repo, d, st):
    """No git: compare the remote commit with what we unpacked last time."""
    try:
        sha = remote_sha(repo)
    except Exception as e:  # noqa: BLE001
        return False, False, st.get("sha"), "could not read remote: %s" % e
    if d.exists() and st.get("sha") == sha:
        return True, False, sha, "up to date"
    url = "https://api.github.com/repos/%s/zipball/%s" % (repo["full_name"], sha)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "foundry"})
        with urllib.request.urlopen(req, timeout=300) as r:
            blob = r.read()
    except Exception as e:  # noqa: BLE001
        return False, False, st.get("sha"), "download failed: %s" % e
    tmp = Path(tempfile.mkdtemp(prefix="foundry-"))
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            z.extractall(tmp)
        inner = next(p for p in tmp.iterdir() if p.is_dir())
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        shutil.move(str(inner), str(d))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not IS_WIN:
        for p in d.rglob("*.command"):
            p.chmod(p.stat().st_mode | 0o111)
    return True, True, sha, ("updated" if st.get("sha") else "downloaded")


def sync(repo, d, st):
    if (d / ".git").exists() or (not d.exists() and have_git()):
        if not have_git():
            return False, False, st.get("sha"), "is a git clone but git is not installed"
        return sync_git(repo, d)
    return sync_zip(repo, d, st)


# ----------------------------------------------------------------------------- build
def artifacts(d):
    dist = d / "dist"
    if not dist.is_dir():
        return []
    if IS_MAC:
        return sorted(p for p in dist.glob("*.app") if p.is_dir())
    if IS_WIN:
        found = list(dist.glob("*.exe"))
        for sub in dist.iterdir():
            if sub.is_dir():
                found.extend(sub.glob("*.exe"))
        return sorted(found)
    return []


def build(name, d, logs, verbose):
    script = d / BUILD_SCRIPT
    logfile = logs / ("%s.log" % name)
    cmd = ["cmd", "/c", str(script)] if IS_WIN else ["bash", str(script)]
    env = dict(os.environ, FOUNDRY="1", CI="1")   # lets a build script skip pauses/opening the app
    start = time.time()
    with open(logfile, "w", encoding="utf-8", errors="replace") as lf:
        lf.write("# %s  %s\n# %s\n\n" % (now(), name, " ".join(cmd)))
        proc = subprocess.Popen(cmd, cwd=str(d), env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1)
        for line in proc.stdout:
            lf.write(line)
            if verbose:
                sys.stdout.write("    | " + line)
                sys.stdout.flush()
        rc = proc.wait()
        lf.write("\n# exit %d after %.0fs\n" % (rc, time.time() - start))
    arts = artifacts(d)
    ok = rc == 0 and bool(arts)
    return ok, rc, arts, logfile, time.time() - start


# ----------------------------------------------------------------------------- shortcuts
def make_shortcuts(root, name, arts):
    apps = root / "_Apps"
    apps.mkdir(exist_ok=True)
    for a in arts:
        if IS_MAC:
            link = apps / a.name
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(a)
        elif IS_WIN:
            lnk = apps / (a.stem + ".lnk")
            ps1 = apps / ".mk.ps1"
            ps1.write_text(
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(%r)\n"
                "$s.TargetPath=%r\n$s.WorkingDirectory=%r\n$s.Save()\n"
                % (str(lnk), str(a), str(a.parent)), encoding="utf-8")
            run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)])
            try:
                ps1.unlink()
            except OSError:
                pass


# ----------------------------------------------------------------------------- self update
def self_update(root, repos, st_all):
    """If foundry itself lives under root and its repo moved, pull and re-exec."""
    try:
        rel = HERE.relative_to(root)
    except ValueError:
        return
    mine = rel.parts[0] if rel.parts else None
    repo = next((r for r in repos if r["name"] == mine), None)
    if not repo:
        return
    d = root / mine
    ok, changed, sha, msg = sync(repo, d, st_all.get(mine))
    st_all.get(mine).update({"sha": sha, "synced": now(), "sync": msg})
    st_all.save()
    if ok and changed and not os.environ.get("FOUNDRY_REEXEC"):
        log("foundry updated itself - restarting")
        os.environ["FOUNDRY_REEXEC"] = "1"
        os.execv(sys.executable, [sys.executable] + sys.argv)


# ----------------------------------------------------------------------------- main
def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", default=cfg["owner"])
    ap.add_argument("--root", default=cfg["root"])
    ap.add_argument("--only", action="append", default=[], metavar="REPO", help="limit to these repos (repeatable)")
    ap.add_argument("--skip", action="append", default=[], metavar="REPO", help="skip these repos this run (repeatable)")
    ap.add_argument("--force", action="store_true", help="rebuild even if nothing changed")
    ap.add_argument("--no-build", action="store_true", help="sync only")
    ap.add_argument("--list", action="store_true", help="show what would happen and exit")
    ap.add_argument("--verbose", "-v", action="store_true", help="stream build output")
    args = ap.parse_args()
    cfg["owner"] = args.owner
    cfg["skip"] = list(cfg["skip"]) + args.skip

    root = Path(os.path.expanduser(args.root)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    st = State(root)

    log("Foundry  %s  owner=%s  root=%s  git=%s  platform=%s"
        % (now(), cfg["owner"], root, "yes" if have_git() else "no (zipball mode)", platform.system()))
    try:
        repos = list_repos(cfg)
    except Exception as e:  # noqa: BLE001
        sys.exit("Could not list repos for %s: %s" % (cfg["owner"], e))
    if args.only:
        wanted = {n.lower() for n in args.only}
        repos = [r for r in repos if r["name"].lower() in wanted]
        missing = wanted - {r["name"].lower() for r in repos}
        if missing:
            log("not found on GitHub (public, non-fork): " + ", ".join(sorted(missing)))
    log("%d repos" % len(repos))

    if args.list:
        for r in repos:
            d = root / r["name"]
            has_script = (d / BUILD_SCRIPT).exists()
            state = "present" if d.exists() else "not downloaded"
            b = "no build script here yet" if not d.exists() else ("buildable" if has_script else "no %s" % BUILD_SCRIPT)
            if r["name"] in cfg["no_build"]:
                b = "build disabled in foundry.json"
            log("  %-32s %-16s %s" % (r["name"], state, b))
        return 0

    if not args.only:
        self_update(root, repos, st)

    results = []
    for r in repos:
        name = r["name"]
        d = root / name
        s = st.get(name)
        log("")
        log("== %s" % name)
        ok, changed, sha, msg = sync(r, d, s)
        s.update({"sha": sha, "synced": now(), "sync": msg})
        log("   sync: %s" % msg)
        st.save()
        if not ok:
            results.append((name, "SYNC FAILED", msg))
            continue
        if args.no_build or name in cfg["no_build"]:
            results.append((name, "synced", msg))
            continue
        if not (d / BUILD_SCRIPT).exists():
            results.append((name, "synced", "no " + BUILD_SCRIPT))
            continue
        need = args.force or changed or s.get("built_sha") != sha or not artifacts(d) or not s.get("build_ok")
        if not need:
            results.append((name, "built", "already built at this commit"))
            continue
        log("   building with %s ... (log: %s)" % (BUILD_SCRIPT, st.logs / (name + ".log")))
        ok, rc, arts, logfile, secs = build(name, d, st.logs, args.verbose)
        s.update({"built_sha": sha, "built": now(), "build_ok": ok, "build_rc": rc,
                  "artifacts": [str(a) for a in arts], "build_seconds": round(secs)})
        st.save()
        if ok:
            log("   built in %dm%02ds: %s" % (secs // 60, secs % 60, ", ".join(a.name for a in arts)))
            if cfg["shortcuts"]:
                try:
                    make_shortcuts(root, name, arts)
                except Exception as e:  # noqa: BLE001
                    log("   shortcut failed: %s" % e)
            results.append((name, "BUILT", ", ".join(a.name for a in arts)))
        else:
            why = "exit %d" % rc if rc else "finished but nothing appeared in dist/"
            log("   BUILD FAILED (%s) - see %s" % (why, logfile))
            results.append((name, "BUILD FAILED", why))

    log("")
    log("Summary  %s" % now())
    width = max((len(n) for n, _, _ in results), default=10)
    for name, status, detail in results:
        log("  %-*s  %-13s %s" % (width, name, status, detail))
    failed = sum(1 for _, s, _ in results if "FAILED" in s)
    if cfg["shortcuts"] and any(s == "BUILT" for _, s, _ in results):
        log("Launchers: %s" % (root / "_Apps"))
    log("Logs: %s" % st.logs)
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("stopped")
