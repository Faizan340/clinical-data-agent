import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    llm_min_interval_s: float = float(os.getenv("LLM_MIN_INTERVAL_S", "13"))
    llm_cache: bool = os.getenv("LLM_CACHE", "false").lower() == "true"
    max_llm_calls: int = int(os.getenv("MAX_LLM_CALLS", "6"))
    max_sql_attempts: int = int(os.getenv("MAX_SQL_ATTEMPTS", "3"))
    max_rows: int = int(os.getenv("MAX_ROWS", "200"))
    admin_db_url: str = os.getenv("ADMIN_DB_URL", "postgresql://admin:admin@localhost:5440/clinical")
    agent_db_url: str = os.getenv("AGENT_DB_URL", "postgresql://analyst_ro:analyst_ro@localhost:5440/clinical")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6390/0")
    semantic_layer_path: Path = Path(__file__).parent / "semantic_layer.yaml"


settings = Settings()
