"""App settings saved in cache/app_settings.json (git-ignored). Never holds secrets: the Canary API
token lives only in Windows Credential Manager."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from .. import config

SETTINGS_FILE = config.REPO_ROOT / "cache" / "app_settings.json"


@dataclass
class AppSettings:
    canary_host: str = config.CANARY_HOST
    read_port: int = config.READ_API_PORT
    write_port: int = config.WRITE_API_PORT
    axiom_url_template: str = "https://localhost/"  # may use {field} and {uwi}
    theme: str = "light"
    operator: str = ""
    notifications: bool = True
    minimise_to_tray: bool = True
    offline: bool = False  # force offline mode (no Canary reads or writes)

    @classmethod
    def load(cls) -> "AppSettings":
        try:
            raw = json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        SETTINGS_FILE.parent.mkdir(exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2))

    def axiom_url(self, field: str = "", uwi: str = "") -> str:
        try:
            return self.axiom_url_template.format(field=field, uwi=uwi)
        except (KeyError, IndexError, ValueError):
            return self.axiom_url_template
