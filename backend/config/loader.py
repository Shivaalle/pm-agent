import os
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, List
import yaml
from dotenv import load_dotenv

# Base directories
CONFIG_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CONFIG_DIR.parent
ROOT_DIR = BACKEND_DIR.parent


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        # 1. Load secrets from .env
        load_dotenv(ROOT_DIR / ".env")

        # 2. Load parameters & metadata from settings.yaml
        yaml_path = CONFIG_DIR / "settings.yaml"
        self._yaml_data: Dict[str, Any] = {}
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                self._yaml_data = yaml.safe_load(f) or {}

        app_cfg = self._yaml_data.get("app", {})
        self.APP_NAME = app_cfg.get("name", "AI Project Manager Agent")
        self.APP_VERSION = app_cfg.get("version", "2.0.0")
        self.APP_ENV = os.getenv("APP_ENV") or app_cfg.get("env", "development")
        self.CORS_ORIGINS: List[str] = [
            origin.strip()
            for origin in (
                os.getenv("CORS_ORIGINS")
                or ",".join(app_cfg.get("cors_origins", []))
            ).split(",")
            if origin.strip()
        ]

        # Secrets
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        self.JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        self.JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
        self.JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
        self.GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
        self.GITHUB_REPO = os.getenv("GITHUB_REPO", "")
        self.TEAMS_ACCESS_TOKEN = os.getenv("TEAMS_ACCESS_TOKEN", "")
        self.TEAMS_TENANT_ID = os.getenv("TEAMS_TENANT_ID", "")
        self.TEAMS_CLIENT_ID = os.getenv("TEAMS_CLIENT_ID", "")
        self.TEAMS_CLIENT_SECRET = os.getenv("TEAMS_CLIENT_SECRET", "")
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

        # AI provider selection (replaceable AI provider rule)
        ai_cfg = self._yaml_data.get("ai", {})
        self.AI_PROVIDER = (os.getenv("AI_PROVIDER") or ai_cfg.get("provider", "gemini")).lower()
        self.AI_ALLOW_MOCK_FALLBACK = _bool(
            os.getenv("AI_ALLOW_MOCK_FALLBACK"), _bool(ai_cfg.get("allow_mock_fallback"), False)
        )
        self.AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES") or ai_cfg.get("max_retries", 1))
        self.AI_TRACK_COST = _bool(os.getenv("AI_TRACK_COST"), _bool(ai_cfg.get("track_cost"), True))
        self.AI_MEASURE_SLA = _bool(os.getenv("AI_MEASURE_SLA"), _bool(ai_cfg.get("measure_sla"), True))
        self.SLA_SECONDS = int(os.getenv("SLA_SECONDS") or ai_cfg.get("sla_seconds", 300))

        # Models (allow .env override if present, else use settings.yaml)
        models_cfg = self._yaml_data.get("models", {})
        self.PRIMARY_MODEL = os.getenv("GEMINI_TEXT_MODEL") or models_cfg.get(
            "primary", "gemini-3.5-flash-lite"
        )
        self.BACKUP_MODEL = os.getenv("GEMINI_TEXT_BACKUP") or models_cfg.get(
            "backup", "gemini-3.7-flash"
        )
        self.TEMPERATURE = float(os.getenv("AI_TEMPERATURE") or models_cfg.get("temperature", 0.1))
        self.MODEL_PRICING: Dict[str, Dict[str, float]] = models_cfg.get("pricing", {}) or {}

        # OpenAI-compatible / self-hosted provider
        oc_cfg = self._yaml_data.get("openai_compatible", {})
        self.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or oc_cfg.get(
            "base_url", "http://localhost:11434/v1"
        )
        self.OPENAI_MODEL = os.getenv("OPENAI_MODEL") or oc_cfg.get("model", "qwen2.5:14b")

        # Jira
        jira_cfg = self._yaml_data.get("jira", {})
        self.JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY") or jira_cfg.get(
            "default_project_key", "KAN"
        )
        self.JIRA_MAX_RESULTS = int(jira_cfg.get("max_results", 50))
        self.JIRA_ISSUE_TYPE = jira_cfg.get("issue_type", "Task")

        # GitHub
        github_cfg = self._yaml_data.get("github", {})
        self.GITHUB_PER_PAGE = int(github_cfg.get("per_page", 25))

        # Teams
        teams_cfg = self._yaml_data.get("teams", {})
        self.TEAMS_ENABLED = _bool(os.getenv("TEAMS_ENABLED"), _bool(teams_cfg.get("enabled"), True))
        self.TEAMS_USER_ID = os.getenv("TEAMS_USER_ID") or teams_cfg.get("default_user_id", "")

        # Scheduler
        scheduler_cfg = self._yaml_data.get("scheduler", {})
        self.ALERT_SCAN_INTERVAL_SECONDS = int(
            os.getenv("ALERT_SCAN_INTERVAL_SECONDS")
            or scheduler_cfg.get("alert_scan_interval_seconds", 60)
        )
        self.SCHEDULER_ENABLED = _bool(
            os.getenv("SCHEDULER_ENABLED"), _bool(scheduler_cfg.get("scheduler_enabled"), True)
        )

        # Baseline project
        proj_cfg = self._yaml_data.get("project_baseline", {})
        self.PROJECT_NAME = proj_cfg.get("name", "AI Project Manager Platform")
        self.AGREED_SCOPE: List[str] = proj_cfg.get("agreed_scope", [])
        self.TEAM_MEMBERS: List[str] = proj_cfg.get("team_members", [])

        # Auth / demo
        self.DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "demo1234")
        self.SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))

        # Connectors may use deterministic mock data only when credentials are
        # absent. A live failure is surfaced instead of silently pretending success.
        self.CONNECTORS_ALLOW_MOCK = _bool(os.getenv("CONNECTORS_ALLOW_MOCK"), True)

        # SQLite database path (overrideable for tests)
        db_override = os.getenv("PM_DB_PATH")
        self.DB_PATH = Path(db_override) if db_override else ROOT_DIR / "data" / "agent.db"
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Static frontend build
        self.FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

    @property
    def jira_live(self) -> bool:
        return bool(
            self.JIRA_BASE_URL
            and self.JIRA_EMAIL
            and self.JIRA_API_TOKEN
            and not self.JIRA_BASE_URL.startswith("https://your-domain")
        )

    @property
    def github_live(self) -> bool:
        return bool(
            self.GITHUB_TOKEN
            and self.GITHUB_REPO
            and not self.GITHUB_REPO.startswith("owner/")
        )

    @property
    def teams_live(self) -> bool:
        return bool(self.TEAMS_ACCESS_TOKEN or (self.TEAMS_TENANT_ID and self.TEAMS_CLIENT_ID and self.TEAMS_CLIENT_SECRET))

    def model_price(self, model: str) -> Dict[str, float]:
        pricing = self.MODEL_PRICING.get(model) or self.MODEL_PRICING.get("default") or {}
        return {
            "input": float(pricing.get("input", 0.30)),
            "output": float(pricing.get("output", 2.50)),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton loader; reads .env and settings.yaml once."""
    return Settings()


# Direct importable singleton
settings = get_settings()
