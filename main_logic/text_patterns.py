# Copyright 2025 Himifox. All Rights Reserved.
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

"""Shared high-precision patterns for interpreting Chinese user input."""

_ZH_A_NOT_A_MODALS = (
    "可以", "能", "能够", "能夠", "会", "會", "该", "該", "应该", "應該",
    "需要", "愿意", "願意", "要", "想", "行", "好", "是", "对", "對",
    "敢", "肯", "值得", "舍得", "捨得", "用", "配", "允许", "允許",
    "乐意", "樂意", "情愿", "情願", "合适", "合適", "方便", "容易",
    "可能", "清楚", "明显", "明顯", "靠谱", "靠譜", "划算", "劃算",
    "合理", "恰当", "恰當",
)


def zh_a_not_a_forms() -> list[str]:
    """Return full and abbreviated A-not-A forms for modal predicates."""
    forms: list[str] = []
    for modal in _ZH_A_NOT_A_MODALS:
        forms.append(f"{modal}不{modal}")
        if len(modal) > 1:
            forms.append(f"{modal[0]}不{modal}")
    forms += ["有没有", "有沒有"]
    return sorted(dict.fromkeys(forms), key=len, reverse=True)
