"""The window must fit the screen and stay reachable. Regression from a real Mac 2026-09-15:
the footer, and so Continue, opened below the bottom edge of a frameless window with no
draggable edges."""
import os, sys
os.environ["QT_QPA_PLATFORM"]="offscreen"
sys.path.insert(0, r"F:\AI_Projects\WaveFlow\app")
from PySide6.QtWidgets import QApplication, QSizeGrip
import wizard as W, settings as S2
FAILS=[]
def check(n,g,w):
    if g!=w: FAILS.append(f"  {n}\n    got  {g!r}\n    want {w!r}")

app=QApplication.instance() or QApplication([])
scr=app.primaryScreen().availableGeometry()
print(f"  screen available: {scr.width()}x{scr.height()}")

w=W.SetupWizard({}, engine=None); w.show(); app.processEvents()
check("wizard fits height", w.height() <= scr.height(), True)
g=w.geometry()
check("wizard fully on screen", scr.contains(g) or w.width() > scr.width(), True)
check("wizard has a size grip", any(isinstance(c,QSizeGrip) for c in w.children()), True)

# the footer (Continue) must be inside the window, not below it
foot=w.findChild(type(w.cfg_primary), None)
check("primary button is inside the window",
      w.cfg_primary.mapTo(w, w.cfg_primary.rect().bottomRight()).y() <= w.height(), True)

# and it must survive a small screen: shrink and re-fit
from wizard_ui import fit_to_screen
fit_to_screen(w, 3000, 2000)      # ask for something absurd
check("absurd size is clamped", w.height() <= scr.height(), True)

s=S2.SettingsWindow({}, engine=None); s.show(); app.processEvents()
MAC_MIN_W, MAC_MIN_H = 1152, 700   # 12-inch MacBook, the smallest modern Mac display
check("settings fits the smallest Mac", s.minimumWidth() <= MAC_MIN_W and s.minimumHeight() <= MAC_MIN_H, True)
check("wizard fits the smallest Mac", w.minimumWidth() <= MAC_MIN_W and w.minimumHeight() <= MAC_MIN_H, True)
# The bug that was actually reported: the footer, and so Continue, below the bottom edge.
check("settings height can shrink well under any Mac", s.minimumHeight() <= 620, True)
check("wizard height can shrink well under any Mac", w.minimumHeight() <= 620, True)
check("settings has a size grip", any(isinstance(c,QSizeGrip) for c in s.children()), True)


# --- the primary button must be ON SCREEN on every page -----------------------------------
# The first fix put the whole page in one scroll area. The window could shrink, but the footer
# scrolled with everything else: Welcome and Where are 913px tall in a 608px viewport, so
# Continue sat at y=862 and was not visible at all. Operator: "the continue button is gone from
# the wizard". The footer now lives OUTSIDE the scroll area; this is what proves it stays there.
PRIMARY = ("Continue", "Get started", "Finish", "Start engine", "Build & start")
for i, page_name in enumerate(["welcome", "where", "configure", "test", "hotkey"]):
    w.go(i)
    app.processEvents()
    pg = w.stack.currentWidget()
    btns = [b for b in pg.findChildren(type(w.cfg_primary)) if b.text() in PRIMARY]
    check(f"{page_name}: has a primary button", bool(btns), True)
    if not btns:
        continue
    b = btns[-1]
    bottom = b.mapTo(w, b.rect().bottomRight()).y()
    top = b.mapTo(w, b.rect().topLeft()).y()
    check(f"{page_name}: primary button is inside the window",
          0 <= top and bottom <= w.height(), True)
    check(f"{page_name}: primary button is visible", b.isVisibleTo(w), True)

if FAILS: print("FIT_FAIL\n"+"\n".join(FAILS)); raise SystemExit(1)
print(f"FIT_OK — wizard min {w.minimumWidth()}x{w.minimumHeight()}, "
      f"settings min {s.minimumWidth()}x{s.minimumHeight()}; both under 1152x700")
