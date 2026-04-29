"""
collectors/config.py
Centralized settings pulled from environment variables.
All collectors import from here — never read env directly.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List


class Settings(BaseSettings):
    # ── Redis ─────────────────────────────────
    redis_url: str = Field("redis://localhost:6379/0", env="REDIS_URL")

    # ── TimescaleDB ───────────────────────────
    timescale_dsn: str = Field(
        "postgresql://inetuser:changeme@localhost:5432/inethealth",
        env="TIMESCALE_DSN"
    )

    # ── Neo4j ─────────────────────────────────
    neo4j_uri: str = Field("bolt://localhost:7687", env="NEO4J_URI")
    neo4j_auth: str = Field("neo4j/changeme_neo4j", env="NEO4J_AUTH")

    # ── Elasticsearch ─────────────────────────
    es_url: str = Field("http://localhost:9200", env="ES_URL")

    # ── MinIO ─────────────────────────────────
    minio_endpoint: str = Field("localhost:9000", env="MINIO_ENDPOINT")
    minio_access_key: str = Field("minioadmin", env="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field("changeme_minio", env="MINIO_SECRET_KEY")
    minio_bucket_mrt: str = "mrt-dumps"

    # ── API keys ──────────────────────────────
    cloudflare_radar_token: str = Field("", env="CLOUDFLARE_RADAR_TOKEN")
    reddit_client_id: str = Field("", env="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field("", env="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field("inet-health-engine/0.1", env="REDDIT_USER_AGENT")
    x_bearer_token: str = Field("", env="X_BEARER_TOKEN")

    # ── BGP collector tuning ──────────────────
    poll_interval: int = Field(300, env="POLL_INTERVAL")
    bgp_collectors: List[str] = Field(
        default=["ripe-ris", "routeviews"],
        env="BGP_COLLECTORS"
    )
    ripe_ris_collectors: List[str] = Field(
        default=["rrc00", "rrc01", "rrc03", "rrc04", "rrc05"],
        env="RIPE_RIS_COLLECTORS"
    )
    # Window of BGP updates to pull per cycle (seconds)
    bgp_window_seconds: int = Field(300, env="BGP_WINDOW_SECONDS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Singleton — import this everywhere
settings = Settings()
