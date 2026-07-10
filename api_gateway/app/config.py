from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    dm_service_url: str = "http://dm-service:8000"
    combat_engine_url: str = "http://combat-engine:8000"
    world_state_url: str = "http://world-state-service:8000"
    story_state_url: str = "http://story-state-service:8000"
    map_service_url: str = "http://map-service:8000"
    memory_service_url: str = "http://memory-service:8000"
    npc_service_url: str = "http://npc-service:8000"
    rules_engine_url: str = "http://rules-engine:8000"
    event_log_url: str = "http://event-log-service:8000"
    service_name: str = "api-gateway"
    log_level: str = "info"
    request_timeout_seconds: float = 10.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()