"""
config.py
---------
Centralised settings loaded from the .env file via pydantic-settings.
All secrets live in .env; this module exposes them as typed attributes.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

from pathlib import Path
from dotenv import load_dotenv
import os

# 1. Get the absolute path of the directory where this script resides
current_dir = Path(__file__).parent.resolve()

# 2. Define the path to the .env file in the subdirectory
env_path = current_dir / '.env'



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_path,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # # Supabase — authentication (JWT) + authorization (RBAC tables)
    # supabase_url: str
    # supabase_anon_key: str
    # supabase_service_role_key: str  # needed to query RBAC tables server-side
    # supabase_jwt_secret: str        # used to verify JWTs without a round-trip

    # Pinecone
    pinecone_api_key: str
    pinecone_index_name: str = "mag7-annual-reports"
    pinecone_environment: str = "us-east-1-aws"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str
    neo4j_database: str = "neo4j"

    # MCP Server
    mcp_server_host: str = "0.0.0.0"
    mcp_server_port: int = 8000
    mcp_server_name: str = "mag7-financial-analyst"
    mcp_log_level: str = "INFO"

    #OPENAI
    azure_openai: str
    azure_openai_endpoint: str
    azure_openai_embeddings_deployment: str = "text-embedding-3-small"
    azure_openai_api_version: str = "2024-12-01-preview"
    


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()