import importlib.util, subprocess, sys, tempfile, os, pathlib
ROOT = pathlib.Path(r"C:/Users/wehos/Project/lanlan_release/Xiao8/.claude/worktrees/relaxed-pike-522c0e")
sys.path.insert(0, str(ROOT))

def load(rel, m):
    src = subprocess.run(["git", "show", "origin/main:" + rel], cwd=ROOT,
                         capture_output=True, encoding="utf-8").stdout
    d = tempfile.mkdtemp()
    p = os.path.join(d, m.split(".")[-1] + ".py")
    open(p, "w", encoding="utf-8").write(src)
    sp = importlib.util.spec_from_file_location(m, p)
    mod = importlib.util.module_from_spec(sp)
    sys.modules[m] = mod
    sp.loader.exec_module(mod)
    return mod

import main_logic.music_requests as nm
import main_routers.card_assist_router as nc
bm = load("main_logic/music_requests.py", "base_music_p75k")
bc = load("main_routers/card_assist_router.py", "main_routers.base_card_p75k")
sys.modules["main_logic.music_requests"] = nm

B = bc._chat_text_requests_full_rewrite
N = nc._chat_text_requests_full_rewrite

cases = [
    "把整个卡啦OK的名字重写",
    "把整个卡通角色的名字重写",
    "把整个卡的名字重写",
    "把整個卡啦OK的名字重寫",
    "重写整个卡啦OK的名字",
    "把整个卡啦OK的介绍改一下",
    "整个卡啦OK的名字重写",
    "把整个卡拉OK的名字重写",
]
print("=" * 74)
print(f"{'sentence':<34} {'base':<7} {'now':<7} class")
print("=" * 74)
for s in cases:
    b, n = B(s), N(s)
    cls = "CLASS-1" if (not b and n) else ("same" if b == n else "less")
    print(f"{s:<34} {str(b):<7} {str(n):<7} {cls}")
print("=" * 74)
