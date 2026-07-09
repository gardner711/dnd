from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    rules_engine_url: str = "http://rules-engine:8000"
    world_state_url: str = "http://world-state-service:8000"
    event_log_url: str = "http://event-log-service:8000"
    service_name: str = "combat-engine"
    log_level: str = "info"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
