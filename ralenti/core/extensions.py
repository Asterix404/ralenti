"""Optional extensions: if a package named ralenti_ext is installed, its register(api) is called once.
Extensions can add engines, settings sections and setup rows."""
from __future__ import annotations

import os
import traceback


class ExtensionAPI:
    def __init__(self):
        self.name = ""
        self.engines = []
        self.settings_sections = []   # factory(settings_page) -> (key, title, icon, widget)
        self.setup_rows = []          # factory(setup_dialog) -> widget with refresh()
        self.disable_updates = False

    def register_engine(self, engine) -> None:
        self.engines.append(engine)

    def add_settings_section(self, factory) -> None:
        self.settings_sections.append(factory)

    def add_setup_row(self, factory) -> None:
        self.setup_rows.append(factory)


_api: ExtensionAPI | None = None


def load() -> ExtensionAPI:
    global _api
    if _api is not None:
        return _api
    _api = ExtensionAPI()
    if os.environ.get("RALENTI_NO_EXTENSIONS"):
        return _api
    try:
        import ralenti_ext
    except ModuleNotFoundError as e:
        if e.name != "ralenti_ext":
            traceback.print_exc()
        return _api
    try:
        ralenti_ext.register(_api)
    except Exception:
        traceback.print_exc()
    return _api
