"""Real-time screen capture — grab the LIVE screen on demand or as a short
burst, so UI (glass, animation, real behavior) can be observed, not just
guessed from static self-grabs. Uses mss (fast). Fails cleanly if the session
is locked (returns a black frame — reported, not silently saved).

    screencap.py --out shot.png                      # full virtual screen
    screencap.py --region X Y W H --out shot.png      # a rectangle
    screencap.py --region X Y W H --frames 8 --interval 0.15 --out anim.png
        -> anim_00.png .. (a burst, for motion)
"""
import argparse
import sys
import time
from pathlib import Path

import mss
import numpy as np
from PIL import Image


def grab(sct, region):
    box = region or sct.monitors[0]  # monitors[0] = the whole virtual screen
    if region:
        box = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
    img = np.array(sct.grab(box))[:, :, :3][:, :, ::-1]  # BGRA -> RGB
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="shot.png")
    ap.add_argument("--region", type=int, nargs=4, default=None, metavar=("X", "Y", "W", "H"))
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--interval", type=float, default=0.15)
    args = ap.parse_args()

    with mss.MSS() as sct:
        first = grab(sct, args.region)
        if first[:, :, :3].mean() < 3:
            print("SCREEN BLACK/LOCKED — capture unavailable (session must be unlocked)")
            return 2
        if args.frames <= 1:
            Image.fromarray(first).save(args.out)
            print(f"saved {args.out} {first.shape[1]}x{first.shape[0]}")
        else:
            stem = Path(args.out)
            for i in range(args.frames):
                img = grab(sct, args.region)
                Image.fromarray(img).save(stem.with_name(f"{stem.stem}_{i:02d}{stem.suffix}"))
                time.sleep(args.interval)
            print(f"saved {args.frames} frames {stem.stem}_NN{stem.suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
