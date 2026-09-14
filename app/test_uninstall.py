"""Uninstall safety — runs in a temp sandbox, never touches the real install.

Run: venv/Scripts/python.exe app/test_uninstall.py -> UNINSTALL_OK
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup_logic as S  # noqa: E402
import uninstall as U  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"  {name}: got {got!r}, want {want!r}")


with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    root, data, hf, outside = t / "WaveFlow", t / "appdata" / "WaveFlow", t / "hf", t / "precious"
    for p in (root / "app", root / "server", root / "docker", data, hf / "hub", outside):
        p.mkdir(parents=True)
    (root / "app" / "config.json").write_text("{}")
    (root / "app" / "waveflow.log").write_text("log")
    (root / "server" / "vocab.user.json").write_text("{}")
    (data / "engine.log").write_text("x")
    model = hf / "hub" / U.MODEL_REPOS[0]
    (model / "snap").mkdir(parents=True)
    (model / "snap" / "encoder.onnx").write_bytes(b"0" * 5000)
    other_model = hf / "hub" / "models--someone--else"
    other_model.mkdir()
    (outside / "keep.txt").write_text("keep")

    with mock.patch.object(S, "ROOT", root), mock.patch.object(U.S, "ROOT", root), \
         mock.patch.object(U, "COMPOSE", root / "docker" / "compose.yml"), \
         mock.patch.object(S, "app_data", lambda: data), mock.patch.dict(os.environ, {"HF_HOME": str(hf)}), \
         mock.patch.object(U, "_docker_has_ours", lambda: False), \
         mock.patch.object(U, "schedule_folder_delete", lambda f: scheduled.append(f)):
        scheduled = []
        items = {i.key: i for i in U.scan(root / "app" / "config.json")}
        check("all six items listed", list(items), ["engine", "models", "docker", "settings", "vocab", "folder"])
        check("models found with size", (items["models"].present, items["models"].size >= 5000), (True, True))
        check("vocab and folder are NOT default", (items["vocab"].default, items["folder"].default), (False, False))
        check("docker absent when none of ours", items["docker"].present, False)
        defaults = {k for k, i in items.items() if i.default}

        U.run(list(items.values()), defaults, dry_run=True, log=lambda *_: None)
        check("dry run deletes nothing", (model.exists(), (root / "app" / "config.json").exists()), (True, True))

        class Eng:
            stopped = False

            def stop(self):
                Eng.stopped = True

        errs = U.run(list(items.values()), defaults, engine=Eng(), log=lambda *_: None)
        check("no errors", errs, [])
        check("engine stopped", Eng.stopped, True)
        check("model deleted", model.exists(), False)
        check("someone else's model kept", other_model.exists(), True)
        check("settings deleted", ((root / "app" / "config.json").exists(), data.exists()), (False, False))
        check("vocab kept (not chosen)", (root / "server" / "vocab.user.json").exists(), True)
        check("folder not scheduled (not chosen)", scheduled, [])
        check("outside untouched", (outside / "keep.txt").exists(), True)

        bad = U.Item("settings", "x", "x", paths=[outside / "keep.txt"])
        errs = U.run([bad], {"settings"}, log=lambda *_: None)
        check("refuses a path outside its own locations", (bool(errs), (outside / "keep.txt").exists()), (True, True))

        U.run([U.Item("folder", "x", "x", default=False, paths=[root])], {"folder"}, log=lambda *_: None)
        check("folder delete is scheduled, not immediate", (scheduled == [root], root.exists()), (True, True))

check("vps remote commands", U.remote_commands({"engine": {"mode": "vps"}})[0].startswith("docker compose -f docker/compose.vps.yml down"), True)
check("local has no remote commands", U.remote_commands({"engine": {"mode": "local"}}), [])
check("docker down only removes local images + our volumes", U.docker_down_cmd()[-3:], ["--rmi", "local", "-v"])

if FAILS:
    print("UNINSTALL_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("UNINSTALL_OK")
