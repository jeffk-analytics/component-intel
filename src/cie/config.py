# src/cie/config.py
"""Single configuration module. All API keys live in environment variables
(optionally via a dev-time .env). A distributor is enabled iff its required
key fields are present — missing Arrow/Avnet keys silently disable those
adapters and must never break a run."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="CIE_", extra="ignore"
    )

    # --- Dashboard ---
    app_passcode: str | None = None   # CIE_APP_PASSCODE; blank = no gate

    # --- OEMsecrets (aggregation spine; free) ---
    oemsecrets_api_key: str | None = None

    # --- Digi-Key (Product Information API V4, OAuth2 client-credentials) ---
    digikey_client_id: str | None = None
    digikey_client_secret: str | None = None

    # --- Mouser (Search API, simple API key) ---
    mouser_api_key: str | None = None

    # --- Arrow (optional enrichment; approval-gated) ---
    arrow_login: str | None = None
    arrow_api_key: str | None = None

    # --- Avnet (optional enrichment; approval-gated) ---
    # Avnet's portal issues: a Subscription Key, plus OAuth client credentials
    # and a Token URL (all shown on the portal Profile page after approval),
    # plus the request URL of the customer-price v1 API (shown on its docs page).
    avnet_subscription_key: str | None = None
    avnet_client_id: str | None = None
    avnet_client_secret: str | None = None
    avnet_token_url: str | None = None
    avnet_price_url: str | None = None

    # --- storage & caching ---
    data_dir: Path = Path("data")
    availability_ttl_hours: float = 4.0   # short TTL: offers/stock/lead times
    static_ttl_days: float = 7.0          # long TTL: identity/parametrics/datasheet URL
    http_timeout_seconds: float = 20.0

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def datasheet_dir(self) -> Path:
        return self.data_dir / "datasheets"

    # --- per-distributor enable flags, derived purely from key presence ---
    @property
    def oemsecrets_enabled(self) -> bool:
        return bool(self.oemsecrets_api_key)

    @property
    def digikey_enabled(self) -> bool:
        return bool(self.digikey_client_id and self.digikey_client_secret)

    @property
    def mouser_enabled(self) -> bool:
        return bool(self.mouser_api_key)

    @property
    def arrow_enabled(self) -> bool:
        return bool(self.arrow_login and self.arrow_api_key)

    @property
    def avnet_enabled(self) -> bool:
        return bool(
            self.avnet_subscription_key
            and self.avnet_client_id
            and self.avnet_client_secret
            and self.avnet_token_url
            and self.avnet_price_url
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
