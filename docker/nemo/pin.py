"""Force every installed package to the exact version in constraints.txt (a tested set).

That set does not satisfy NeMo's own pins (e.g. fsspec 2026.4.0 vs nemo's ==2024.12.0), so a
resolver can never reproduce it. Reproduce it literally instead: for each installed
distribution listed at a different version, reinstall that exact version with --no-deps.
Prints what it changed; exits non-zero if anything still differs.
"""
import importlib.metadata as md
import re
import subprocess
import sys


def canon(n):
    return re.sub(r"[-_.]+", "-", n).lower()


live = {}
for line in open("/tmp/constraints.txt"):
    line = line.strip()
    if "==" in line:
        name, ver = line.split("==", 1)
        live[canon(name)] = ver

have = {canon(d.metadata["Name"]): d.version for d in md.distributions()}
todo = sorted(f"{n}=={live[n]}" for n in have if n in live and have[n] != live[n])
print("repinning", len(todo), todo, flush=True)
if todo:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", "--no-deps", *todo])
have = {canon(d.metadata["Name"]): d.version for d in md.distributions()}
bad = [n for n in have if n in live and have[n] != live[n]]
print("still different:", bad, flush=True)
sys.exit(1 if bad else 0)
