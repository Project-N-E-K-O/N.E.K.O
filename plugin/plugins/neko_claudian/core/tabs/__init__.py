"""core.tabs (仿 claudian/src/core/tabs/)"""

from .tab import (
    create_tab,
    activate_tab,
    deactivate_tab,
    destroy_tab,
    get_tab_title,
    initialize_tab_ui,
    initialize_tab_controllers,
    initialize_tab_service,
    setup_service_callbacks,
    wire_tab_input_events,
)
from .tab_manager import TabManager
from .provider_resolution import get_tab_provider_id
from .types import (
    MIN_TABS,
    MAX_TABS,
    DEFAULT_MAX_TABS,
    TabId,
    TabData,
    TabBarItem,
    PersistedTabState,
    PersistedTabManagerState,
    TabManagerCallbacks,
    ForkContext,
)
