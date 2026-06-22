from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Branch:
    name: str
    ctmnumb: str
    capacity: int


@dataclass
class Config:
    branches: list[Branch]
    spreadsheet_id: str
    data_tab: str
    report_tab: str
    monthly_tab: str
    slack_enabled: bool
    slack_channel_name: str
    portal_url: str
    login_proc_url: str

    @classmethod
    def load(cls, path: Path) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        branches = [Branch(**b) for b in raw["branches"]]
        return cls(
            branches=branches,
            spreadsheet_id=raw["google_sheet"]["spreadsheet_id"],
            data_tab=raw["google_sheet"]["data_tab"],
            report_tab=raw["google_sheet"]["report_tab"],
            monthly_tab=raw["google_sheet"]["monthly_tab"],
            slack_enabled=raw["slack"]["enabled"],
            slack_channel_name=raw["slack"]["channel_name"],
            portal_url=raw["carefor"]["portal_url"],
            login_proc_url=raw["carefor"]["login_proc_url"],
        )


def app_data_dir() -> Path:
    """OneDrive 동기화 밖에 위치한 로컬 데이터 디렉토리."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    p = Path(base) / "carefor-auto"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return app_data_dir() / "config.yaml"


def google_credentials_path() -> Path:
    return app_data_dir() / "google_service_account.json"


def log_dir() -> Path:
    p = app_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
