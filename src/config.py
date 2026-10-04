from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude & OpenAI
    anthropic_api_key: str
    openai_api_key: str
    
    # Supabase
    supabase_url: str
    supabase_key: str
    
    # Redis
    redis_url: str = "redis://localhost:6379"
    
    # Server
    max_call_duration_seconds: int = 600
    agent_timeout_seconds: int = 30
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
