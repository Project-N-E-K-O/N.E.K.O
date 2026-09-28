"""Compatibility alias: the pre-split home of the QQ Open Platform connection.

Importing this module yields :mod:`utils.connection.qq.open_platform` itself
(the module object is swapped in ``sys.modules``), so attribute reads *and*
``monkeypatch.setattr`` on module globals keep reaching the real implementation.
A re-exporting module would silently break the latter. New code should import
from :mod:`utils.connection.qq`.
"""

from __future__ import annotations

import sys

from ..qq import open_platform as _open_platform

sys.modules[__name__] = _open_platform
