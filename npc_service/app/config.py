from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/gamedb"
    redis_url: str = "redis://localhost:6379"
    event_log_url: str = "http://event-log-service:8000"
    world_state_url: str = "http://world-state-service:8000"
    story_state_url: str = "http://story-state-service:8000"
    memory_service_url: str = "http://memory-service:8000"
    service_name: str = "npc-service"
    dialogue_history_limit: int = 20
    log_level: str = "info"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
