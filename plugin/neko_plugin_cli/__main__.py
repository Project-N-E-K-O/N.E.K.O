"""Allow ``python -m plugin.neko_plugin_cli``."""

from .cli import main

raise SystemExit(main())
