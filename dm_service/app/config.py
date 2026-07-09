from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:postgres@localhost:5432/gamedb"
    story_state_url: str = "http://story-state-service:8000"
    world_state_url: str = "http://world-state-service:8000"
    npc_service_url: str = "http://npc-service:8000"
    memory_service_url: str = "http://memory-service:8000"
    map_service_url: str = "http://map-service:8000"
    combat_engine_url: str = "http://combat-engine:8000"
    event_log_url: str = "http://event-log-service:8000"
    llm_provider: str = "stub"
    llm_model: str = "dm-stub-v1"
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_timeout_seconds: float = 20.0
    llm_temperature: float = 0.2
    service_name: str = "dm-service"
    log_level: str = "info"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()