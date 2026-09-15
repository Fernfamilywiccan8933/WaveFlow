"""Uninstall WaveFlow from this PC. Used by the app (⚙ → Setup… → Uninstall) and on its own:

    venv\\Scripts\\python app\\uninstall.py            # lists what would be removed, asks first
    venv\\Scripts\\python app\\uninstall.py --dry-run  # lists only

Removes ONLY what WaveFlow created: its engine process, its downloaded models, its own Docker
containers/images/volumes, its settings and logs, and — only if chosen — the personal vocabulary
and the app folder. A server elsewhere (onsite/VPS) is never touched; its commands are printed.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup_logic as S  # noqa: E402

MODEL_REPOS = ["models--istupakov--parakeet-tdt-0.6b-v2-onnx", "models--nvidia--parakeet-tdt-0.6b-v2"]
COMPOSE = S.ROOT / "docker" / "compose.yml"
NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Item:
    key: str
    title: str
    detail: str
    size: int = 0
    default: bool = True
    paths: list[Path] = field(default_factory=list)
    present: bool = True
    cfg: dict = field(default_factory=dict)      # "server": the saved config that says where it was installed


def hf_hub() -> Path:
    """Where THIS copy's engine downloads models (local_engine sets HF_HOME to it)."""
    # NEVER the user's own HF_HOME: local_engine always points the engine at models_dir(), so every model
    # WaveFlow downloaded is there. A shared Hugging Face cache belongs to other apps too.
    return S.models_dir() / "hub"


def size_of(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def human(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def docker_base_images() -> list[str]:
    """The public base images our Dockerfiles pulled (FROM lines), e.g. python:3.12-slim-bookworm."""
    out = []
    for df in sorted((S.ROOT / "docker").glob("*/Dockerfile")):
        try:
            for line in df.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].upper() == "FROM" and parts[1] not in out and ":" in parts[1]:
                    out.append(parts[1])
        except OSError:
            pass
    return out


def docker_base_rm_cmd(image: str) -> list[str]:
    # No --force: Docker refuses while ANY container (another app's included) still uses the image,
    # so a base image something else depends on is always left in place.
    return ["docker", "image", "rm", image]


def docker_down_cmd() -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE), "--profile", "nemo", "--profile", "onnx-gpu",
            "down", "--rmi", "all", "-v"]     # images are named in compose.yml, so "local" would keep them


def _docker_has_ours() -> bool:
    if not shutil.which("docker") or not COMPOSE.exists():
        return False
    try:
        r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--profile", "nemo", "--profile", "onnx-gpu",
                            "ps", "-a", "-q"], capture_output=True, text=True, timeout=20, creationflags=NOWINDOW,
                           env={**os.environ, "WAVEFLOW_TOKEN": "x"})   # compose refuses to parse without it
        return bool(r.stdout.strip())
    except Exception:
        return False


def scan(config_path: Path | None = None) -> list[Item]:
    config_path = config_path or (S.ROOT / "app" / "config.json")
    data = S.app_data()
    models = [hf_hub() / r for r in MODEL_REPOS if (hf_hub() / r).exists()]
    settings = [p for p in (config_path, data / "docker.env", data / "engine.log",
                            S.ROOT / "app" / "waveflow.log") if p.exists()]
    vocab = [p for p in (S.ROOT / "server" / "vocab.user.json",) if p.exists()]
    shortcuts = S.shortcuts_ours()
    try:
        import json
        cfg = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except (OSError, ValueError):
        cfg = {}
    target = remote_target(cfg)
    items = [
        Item("server", f"Server install on {target[1]}" if target else "Server install",
             f"{target[0]}@{target[1]}:{target[2]} — container, image, volumes, files (over SSH)" if target
             else "none set up by WaveFlow", 0, True, [], bool(target), cfg),
        Item("engine", "Local engine", "stop it if it is running", 0, True, [], True),
        Item("models", "Downloaded models", ", ".join(str(m) for m in models) or "none found",
             sum(size_of(m) for m in models), True, models, bool(models)),
        Item("docker", "Docker containers, images and volumes", "only the ones WaveFlow created",
             0, True, [], _docker_has_ours()),
        Item("settings", "Settings and logs", ", ".join(p.name for p in settings) or "none found",
             sum(size_of(p) for p in settings), True, settings + ([data] if data.exists() else []),
             bool(settings) or data.exists()),
        Item("shortcuts", "Shortcuts and start with Windows",
             "Start menu and desktop shortcuts, and the start-with-Windows entry", 0, True, shortcuts,
             bool(shortcuts) or S.autostart_enabled()),
        Item("vocab", "Personal vocabulary", "server\\vocab.user.json", sum(size_of(p) for p in vocab),
             False, vocab, bool(vocab)),
        # Default ON: without it the code stays and can be started again with nothing set up.
        Item("folder", "The app folder", f"{S.ROOT} — removed after the app closes", 0, True, [S.ROOT], True),
    ]
    return items


def remote_target(cfg: dict) -> tuple[str, str, str] | None:
    """(user, host, folder) when setup installed the server over SSH, else None."""
    e = cfg.get("engine") or {}
    if e.get("mode") not in ("onsite", "vps") or e.get("method") == "venv":
        return None
    import remote_install as RI
    user, folder, host = e.get("ssh_user", ""), e.get("folder", ""), RI.host_of(cfg.get("url", ""))
    if RI.validate_target(user, host, folder) or folder.rstrip("/") in ("~", "", "/", "."):
        return None
    return user, host, folder


def remote_uninstall_script(cfg: dict) -> str:
    """Runs ON the server over SSH. Removes only what the wizard put there: our containers, our named
    images, our volumes, the base images our Dockerfiles pulled (skipped while anything else uses them),
    and the install folder — and only if that folder really holds our compose file."""
    _user, _host, folder = remote_target(cfg)
    vps = (cfg.get("engine") or {}).get("mode") == "vps"
    compose = "docker/compose.vps.yml" if vps else "docker/compose.yml --profile nemo --profile onnx-gpu"
    bases = " ".join(docker_base_images())
    return (f"if [ -f {folder}/docker/compose.yml ]; then "
            f"cd {folder} && docker compose --env-file docker/.env -f {compose} down --rmi all -v; "
            f"for i in {bases}; do docker image rm $i >/dev/null 2>&1 && echo \"removed $i\" "
            f"|| echo \"kept $i (in use or not present)\"; done; "
            f"cd ~ && rm -rf {folder} && echo 'removed {folder}'; "
            f"else echo 'nothing installed at {folder}'; fi")


def remote_commands(cfg: dict) -> list[str]:
    """What will run on the server (shown in the preview), or the manual steps for a venv install."""
    e = cfg.get("engine") or {}
    if remote_target(cfg):
        user, host, _folder = remote_target(cfg)
        return [f"ssh {user}@{host}", remote_uninstall_script(cfg).replace("; ", ";\n  ")]
    if e.get("mode") == "vps":
        return ["docker compose -f docker/compose.vps.yml down --rmi all -v"]
    if e.get("mode") == "onsite":
        if e.get("method") == "venv":
            return ["# stop the parakeet_server.py process, then delete the folder you cloned"]
        return ["docker compose -f docker/compose.yml --profile nemo --profile onnx-gpu down --rmi all -v"]
    return []


def remove_remote(cfg: dict, log=print, timeout=600) -> list[str]:
    target = remote_target(cfg)
    if not target:
        return []
    import remote_install as RI
    user, host, _folder = target
    log(f"ssh {user}@{host}: removing the server install")
    try:
        r = subprocess.run(RI.ssh_cmd(user, host, remote_uninstall_script(cfg)), capture_output=True, text=True,
                           timeout=timeout, creationflags=NOWINDOW)
    except subprocess.TimeoutExpired:
        return [f"server {host}: did not finish within {timeout // 60} minutes"]
    for line in r.stdout.splitlines()[-20:]:
        log(line)
    if r.returncode == 255:
        return [f"server {host}: " + RI.classify_ssh_error(r.stderr)[1]]
    return []


def plan_lines(items: list[Item], chosen: set[str]) -> list[str]:
    out = []
    for it in items:
        if it.key not in chosen or not it.present:
            continue
        if it.key == "server":
            out += ["# on the server, over SSH:"] + remote_commands(it.cfg) + \
                   ["# not removed: Docker's shared build cache (other builds use it too)"]
        elif it.key == "engine":
            out.append("stop the local engine")
        elif it.key == "docker":
            out.append(" ".join(docker_down_cmd()))
            out += [" ".join(docker_base_rm_cmd(i)) + "   # skipped if anything else uses it"
                    for i in docker_base_images()]
        elif it.key == "folder":
            out.append(f"after the app closes: delete {S.ROOT}")
        elif it.key == "shortcuts":
            out += [f"delete {p}" for p in it.paths]
            if S.autostart_enabled():
                out.append("remove start with Windows")
        else:
            out += [f"delete {p}" for p in it.paths]
    return out


def release_logs():
    """Close this process's own log files inside WaveFlow's folders. Windows will not delete a file
    that is still open, and the running app keeps waveflow.log open for its whole life."""
    root = logging.getLogger()
    for lg in [root] + [logging.getLogger(n) for n in list(logging.root.manager.loggerDict)]:
        for h in list(getattr(lg, "handlers", [])):
            if isinstance(h, logging.FileHandler) and _inside_allowed(Path(h.baseFilename)):
                lg.removeHandler(h)
                h.close()


def run(items: list[Item], chosen: set[str], engine=None, dry_run=False, log=print) -> list[str]:
    """Remove the chosen items. Returns error lines (empty = all done).
    Order matters: stop the engine and close log files BEFORE deleting anything they hold open."""
    errors = []
    if not dry_run:
        if engine is not None:
            engine.stop()
        if chosen & {"engine", "models", "settings", "folder"}:
            from local_engine import stop_all_engines
            stopped = stop_all_engines()      # also engines an earlier run left behind
            if stopped:
                log(f"stopped {stopped} engine process(es) left running from this folder")
        if chosen & {"settings", "folder"}:
            release_logs()
    for it in items:
        if it.key not in chosen or not it.present:
            continue
        if it.key == "server":
            if not dry_run:
                errors += remove_remote(it.cfg, log=log)
            else:
                log("would remove the server install over SSH")
        elif it.key == "engine":
            if engine is not None and not dry_run:
                engine.stop()
            log("stopped the local engine")
        elif it.key == "docker":
            log("running: " + " ".join(docker_down_cmd()))
            if not dry_run:
                r = subprocess.run(docker_down_cmd(), capture_output=True, text=True, creationflags=NOWINDOW,
                                   env={**os.environ, "WAVEFLOW_TOKEN": "x"})
                if r.returncode != 0:
                    errors.append("docker: " + (r.stderr or r.stdout).strip()[-200:])
                else:
                    for image in docker_base_images():
                        rr = subprocess.run(docker_base_rm_cmd(image), capture_output=True, text=True,
                                            creationflags=NOWINDOW)
                        log(f"base image {image}: " + ("removed" if rr.returncode == 0 else
                                                       "kept (in use by something else, or not present)"))
        elif it.key == "shortcuts":
            for p in it.paths:
                if p.name != S.SHORTCUT_NAME:          # only our own named .lnk files
                    errors.append(f"refused to delete {p}: not a WaveFlow shortcut")
                    continue
                log(f"delete {p}")
                if not dry_run:
                    try:
                        p.unlink(missing_ok=True)
                    except OSError as e:
                        errors.append(f"{p}: {e}")
            if S.autostart_enabled():
                log("remove start with Windows")
                if not dry_run:
                    try:
                        S.set_autostart(False)
                    except OSError as e:
                        errors.append(f"start with Windows: {e}")
        elif it.key == "folder":
            if not dry_run:
                schedule_folder_delete(S.ROOT)
            log(f"scheduled: delete {S.ROOT} after exit")
        else:
            for p in it.paths:
                if not _inside_allowed(p):
                    errors.append(f"refused to delete {p}: outside WaveFlow's own locations")
                    continue
                log(f"delete {p}")
                if dry_run:
                    continue
                try:
                    shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
                except OSError as e:
                    errors.append(f"{p}: {e}")
    return errors


def _inside_allowed(p: Path) -> bool:
    """Never delete anything outside the app folder, its app-data folder, or its two model repos."""
    p = p.resolve()
    roots = [S.ROOT.resolve(), S.app_data().resolve()] + [(hf_hub() / r).resolve() for r in MODEL_REPOS]
    return any(p == r or r in p.parents for r in roots)


def schedule_folder_delete(folder: Path):
    """The running app lives in this folder, so it cannot delete itself. A detached shell waits
    for the app to exit, then removes the folder."""
    folder = folder.resolve()
    if os.name == "nt":
        q = str(folder).replace("'", "''")          # PowerShell single-quote escape
        # Wait for THIS process to really exit (its python.exe lives in the folder's venv and stays
        # locked until then), then retry for a while in case a child is still closing.
        ps = (f"try {{ Wait-Process -Id {os.getpid()} -Timeout 120 -ErrorAction Stop }} catch {{}}; "
              f"for ($i = 0; $i -lt 30 -and (Test-Path -LiteralPath '{q}'); $i++) {{ "
              f"Remove-Item -LiteralPath '{q}' -Recurse -Force -ErrorAction SilentlyContinue; "
              f"Start-Sleep -Seconds 2 }}")
        # NOT DETACHED_PROCESS: PowerShell with no console exits at once and deletes nothing (measured).
        # A hidden console in its own group, broken away from the app's job so it outlives the app.
        cmd = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]
        base = subprocess.CREATE_NEW_PROCESS_GROUP | NOWINDOW
        try:
            subprocess.Popen(cmd, creationflags=base | 0x01000000, cwd=str(folder.parent))   # CREATE_BREAKAWAY_FROM_JOB
        except OSError:
            subprocess.Popen(cmd, creationflags=base, cwd=str(folder.parent))                 # job forbids breakaway
    else:
        subprocess.Popen(["sh", "-c", f'sleep 5; rm -rf "{folder}"'], start_new_session=True, cwd=str(folder.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description="Uninstall WaveFlow from this PC")
    ap.add_argument("--dry-run", action="store_true", help="list what would be removed, remove nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask")
    ap.add_argument("--vocab", action="store_true", help="also delete server/vocab.user.json")
    ap.add_argument("--folder", action="store_true", help="also delete the app folder")
    args = ap.parse_args()
    items = scan()
    chosen = {it.key for it in items if it.default}
    if args.vocab:
        chosen.add("vocab")
    if args.folder:
        chosen.add("folder")
    for it in items:
        mark = "x" if it.key in chosen and it.present else " "
        print(f"[{mark}] {it.title:40} {human(it.size) if it.size else '':>9}  {it.detail}")
    print("\nWill do:\n  " + "\n  ".join(plan_lines(items, chosen) or ["nothing"]))
    if args.dry_run:
        return 0
    if not args.yes and input("\nType UNINSTALL to continue: ").strip() != "UNINSTALL":
        print("cancelled")
        return 1
    errs = run(items, chosen)
    print("\n".join(errs) if errs else "done")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
