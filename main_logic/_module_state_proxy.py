# -- coding: utf-8 --
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

"""Keep mutable package re-exports bound to their canonical owner modules."""

import sys
from types import ModuleType
from typing import Mapping


def install_state_proxy(package_name: str, state_owners: Mapping[str, str]) -> None:
    """Forward reads, writes, and deletes for exported state to owner modules."""
    package = sys.modules[package_name]
    owner_modules = {
        name: sys.modules[f"{package_name}.{owner_name}"]
        for name, owner_name in state_owners.items()
    }

    class _StateProxyModule(ModuleType):
        def __getattribute__(self, name: str):
            owner = owner_modules.get(name)
            if owner is not None:
                return getattr(owner, name)
            return super().__getattribute__(name)

        def __setattr__(self, name: str, value) -> None:
            owner = owner_modules.get(name)
            if owner is not None:
                setattr(owner, name, value)
                return
            super().__setattr__(name, value)

        def __delattr__(self, name: str) -> None:
            owner = owner_modules.get(name)
            if owner is not None:
                delattr(owner, name)
                return
            super().__delattr__(name)

    package.__class__ = _StateProxyModule
