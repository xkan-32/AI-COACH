from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    gcp_project_id: str = ""
    gcp_region: str = "asia-northeast1"
    bigquery_dataset: str = "training_coach"
    vertex_model: str = "gemini-2.5-flash"
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_verify_token: str = "replace-me"
    strava_redirect_uri: str = "http://localhost:8080/oauth/strava/callback"
    oauth_state_signing_key: str = "local-development-only"
    token_encryption_key: str = ""
    route_fingerprint_key: str = ""
    firestore_database: str = "(default)"
    cloud_tasks_queue_path: str = ""
    worker_url: str = ""
    task_service_account_email: str = ""
    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
