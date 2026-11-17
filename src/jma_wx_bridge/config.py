"""Application configuration."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables (prefix: ``JMA_WX_``)."""

    model_config = SettingsConfigDict(env_prefix="JMA_WX_", env_file=".env", extra="ignore")

    db_path: Path = Path("jma_wx_bridge.db")
    geojson_path: Path | None = None
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    jma_regular_feed_url: str = "https://www.data.jma.go.jp/developer/xml/feed/regular.xml"
    poll_interval_minutes: int = 20
    cache_retention_days: int = 3
    enable_background_poll: bool = True
    request_timeout_seconds: float = 10.0
