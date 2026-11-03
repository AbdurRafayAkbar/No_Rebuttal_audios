"""Settings loaded from `.env` (secrets) and environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Campaign:
    code: str
    name: str
    source_values: tuple[str, ...]  # call_records.campaign_id values
    clips_dir: Path


# Campaign map (D18). Only Medicare has clips so far.
CAMPAIGNS: dict[str, Campaign] = {
    "MEDICARE": Campaign("MEDICARE", "Medicare", ("001camp", "001"), ROOT / "campaigns" / "medicare"),
}


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    deepgram_api_key: str
    llm_endpoint: str
    llm_model: str
    llm_api_key: str
    audio_url_prefix: str
    log_url_prefix: str
    run_tz: str
    shift_start: str
    shift_end: str
    workers: int
    data_dir: Path
    campaigns: dict[str, Campaign] = field(default_factory=lambda: CAMPAIGNS)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_endpoint)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nra.db"


def load_settings(env_file: Path | None = None) -> Settings:
    file_values = _load_env_file(env_file or ROOT / ".env")

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, file_values.get(key, default))

    data_dir = Path(get("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    return Settings(
        db_host=get("DB_HOST"),
        db_port=int(get("DB_PORT", "3306")),
        db_user=get("DB_USER"),
        db_password=get("DB_PASSWORD"),
        db_name=get("DB_NAME", "call_data"),
        deepgram_api_key=get("DEEPGRAM_API_KEY"),
        llm_endpoint=get("LLM_ENDPOINT").rstrip("/"),
        llm_model=get("LLM_MODEL", "/models/gpt-oss-120b"),
        llm_api_key=get("LLM_API_KEY"),
        audio_url_prefix=get("AUDIO_URL_PREFIX", "https://hel1.your-objectstorage.com/sd-bucket/"),
        log_url_prefix=get("LOG_URL_PREFIX", "https://hel1.your-objectstorage.com/sd-logs/"),
        run_tz=get("RUN_TZ", "Asia/Karachi"),
        shift_start=get("SHIFT_START", "18:00"),
        shift_end=get("SHIFT_END", "04:00"),
        workers=int(get("WORKERS", "16")),
        data_dir=data_dir,
    )
