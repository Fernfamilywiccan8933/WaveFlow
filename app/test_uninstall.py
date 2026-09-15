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
         mock.patch.object(S, "app_data", lambda: data), mock.patch.object(S, "models_dir", lambda: hf), \
         mock.patch.dict(os.environ, {"HF_HOME": str(outside)}), \
         mock.patch.object(U, "_docker_has_ours", lambda: False), \
         mock.patch.object(U, "schedule_folder_delete", lambda f: scheduled.append(f)), \
         mock.patch.object(S, "shortcut_paths", lambda: [t / "startmenu" / S.SHORTCUT_NAME,
                                                          t / "desktop" / S.SHORTCUT_NAME]), \
         mock.patch.object(S, "autostart_enabled", lambda: autostart[0]), \
         mock.patch.object(S, "set_autostart", lambda on: autostart.__setitem__(0, on)):
        scheduled, autostart = [], [False]
        (root / "app" / "assets").mkdir()
        # REAL shortcuts (PowerShell + WScript.Shell), written into the temp folders only.
        check("shortcuts created", S.create_shortcuts(), [])
        check("both shortcuts exist and point at this copy", len(S.shortcuts_ours()), 2)
        foreign = t / "desktop" / "Other.lnk"
        foreign.write_text("not ours")
        autostart[0] = True
        items = {i.key: i for i in U.scan(root / "app" / "config.json")}
        check("all items listed", list(items),
              ["engine", "models", "docker", "settings", "shortcuts", "vocab", "folder"])
        check("shortcuts item found", (items["shortcuts"].present, len(items["shortcuts"].paths)), (True, 2))
        U.run([items["shortcuts"]], {"shortcuts"}, log=lambda *_: None)
        check("shortcuts removed, autostart off, other .lnk kept",
              (len(S.shortcuts_ours()), autostart[0], foreign.exists()), (0, False, True))
        items = {i.key: i for i in U.scan(root / "app" / "config.json")}
        check("models found with size", (items["models"].present, items["models"].size >= 5000), (True, True))
        check("vocab NOT default, app folder IS default", (items["vocab"].default, items["folder"].default),
              (False, True))
        check("docker absent when none of ours", items["docker"].present, False)
        defaults = {k for k, i in items.items() if i.default} - {"folder"}

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

        # Regression 2026-09-14 (operator's real uninstall): the running app held waveflow.log and
        # engine.log open, Windows refused to delete them, and the folder was never removed.
        import logging
        from local_engine import LocalEngine
        data.mkdir(parents=True, exist_ok=True)
        (root / "app" / "config.json").write_text("{}")
        h = logging.FileHandler(str(root / "app" / "waveflow.log"))
        logging.getLogger().addHandler(h)
        logging.getLogger().warning("app is running")
        eng = LocalEngine()
        eng._logf = open(eng.log_path, "w")
        items = {it.key: it for it in U.scan(root / "app" / "config.json")}
        errs = U.run(list(items.values()), {"settings"}, engine=eng, log=lambda *_: None)
        check("open log files do not block uninstall", errs, [])
        check("logs gone", ((root / "app" / "waveflow.log").exists(), data.exists()), (False, False))
        check("engine log handle closed", eng._logf, None)
        check("app log handler released", h in logging.getLogger().handlers, False)

        U.run([U.Item("folder", "x", "x", default=False, paths=[root])], {"folder"}, log=lambda *_: None)
        check("folder delete is scheduled, not immediate", (scheduled == [root], root.exists()), (True, True))

check("vps remote commands", U.remote_commands({"engine": {"mode": "vps"}})[0].startswith("docker compose -f docker/compose.vps.yml down"), True)
check("local has no remote commands", U.remote_commands({"engine": {"mode": "local"}}), [])
check("docker down only removes local images + our volumes", U.docker_down_cmd()[-3:], ["--rmi", "local", "-v"])

if FAILS:
    print("UNINSTALL_FAIL\n" + "\n".join(FAILS))
    raise SystemExit(1)
print("UNINSTALL_OK")
