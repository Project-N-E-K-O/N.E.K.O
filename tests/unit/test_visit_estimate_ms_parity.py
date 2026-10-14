# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``estimateSpeechMs`` in text-mouth-driver.js equals ``utils.visit_wire.estimate_speech_ms``.

The backend releases ``paced:'estimate'`` subtitle pieces on the Python
estimate; the page opens the mouth for the JS copy of the same formula
(design §5 PR-13, duality table "Python / JS"). Both sides run over the same
fixed vectors (hand-picked edge cases plus seeded random mixes) and the JS
constants are read back against ``config.visit_settings``.
"""

import json
import random
import re
import shutil
from pathlib import Path

import pytest

import config.visit_settings as vs
from tests.node_harness import run_node_script
from utils.visit_wire import estimate_speech_ms

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DRIVER_PATH = PROJECT_ROOT / "static" / "visit" / "text-mouth-driver.js"

HAND_PICKED = [
    "",
    "你好",
    "今天天气真好。",
    "嗯，嗯，好吧……",
    "こんにちは！",
    "ｶﾀｶﾅ",
    "안녕하세요?",
    "Hello world.",
    "don't stop",
    "3.14 and 1,000",
    "e.g. this",
    "word.",
    "'quoted'",
    "Wait!?! Really?",
    "a,b,c",
    "，，，",
    "。。。！！",
    "Привет, мир!",
    "数字123和abc混合。",
    "😀😀 emoji 😀",
    "x" * 3,
    "长" * 100,
    "word " * 80,
    "tab\tand\nnewline",
    "1.2.3.4",
    "re-enter the room",
    "Ünïcödé wörds",
    "中文，English, 日本語。",
    "²³ superscripts",
    "Ⅻ roman",
]

ALPHABET = list("的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要她出也得里后自以会")
ALPHABET += list("あいうえおかきくけこアイウエオ가나다라마바사abcdefghijklmnopqrstuvwxyzABC0123456789")
ALPHABET += list("。！？；…!?;.，,、'’ -—()（）「」\n")


def _vectors() -> list[str]:
    rng = random.Random(20261011)
    out = list(HAND_PICKED)
    while len(out) < 50:
        out.append("".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 60))))
    return out


SCRIPT = r"""
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync(__DRIVER__, 'utf8');
const win = { addEventListener() {} };
win.window = win;
const sandbox = { window: win, console, performance: { now: () => 0 } };
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'text-mouth-driver.js' });
const vectors = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(vectors.map((v) => win.nekoVisitTextMouth.estimateSpeechMs(v))));
"""


def test_js_estimate_matches_python_on_fixed_vectors():
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is required for the estimate parity check")
    vectors = _vectors()
    assert len(vectors) == 50
    result = run_node_script(
        node_path, SCRIPT.replace("__DRIVER__", json.dumps(str(DRIVER_PATH))),
        input=json.dumps(vectors), cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    js = json.loads(result.stdout)
    py = [estimate_speech_ms(v) for v in vectors]
    mismatches = [(v, p, j) for v, p, j in zip(vectors, py, js) if p != j]
    assert not mismatches, mismatches
    # 向量要真的覆盖到两端夹值与中间值，否则「相等」证明不了什么
    assert vs.VISIT_CLAUSE_MIN_MS in py and vs.VISIT_CLAUSE_MAX_MS in py
    assert len(set(py)) >= 20


@pytest.mark.parametrize("name", [
    "VISIT_CJK_MS_PER_CHAR", "VISIT_LATIN_MS_PER_WORD", "VISIT_PUNCT_END_MS",
    "VISIT_PUNCT_COMMA_MS", "VISIT_CLAUSE_MIN_MS", "VISIT_CLAUSE_MAX_MS",
])
def test_js_constants_equal_visit_settings(name):
    src = DRIVER_PATH.read_text(encoding="utf-8")
    match = re.search(rf"var {name} = (\d+);", src)
    assert match, name
    assert int(match.group(1)) == getattr(vs, name)
