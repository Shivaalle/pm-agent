import os
from pathlib import Path
from functools import lru_cache
from typing import Dict, Any, List
import yaml
from dotenv import load_dotenv

# Base directories
CONFIG_DIR = Path(__file__).resolve().parent
BACKEND_DIR = CONFIG_DIR.parent
ROOT_DIR = BACKEND_DIR.parent

class Settings:
    def __init__(self):
        # 1. Load Secrets from .env
        load_dotenv(ROOT_DIR / ".env")
        
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        self.JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        self.JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
        self.JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
        self.GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
        self.GITHUB_REPO = os.getenv("GITHUB_REPO", "")

        # 2. Load Parameters & Metadata from settings.yaml
        yaml_path = CONFIG_DIR / "settings.yaml"
        self._yaml_data: Dict[str, Any] = {}
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                self._yaml_data = yaml.safe_load(f) or {}

        # Models (allow .env override if present, else use settings.yaml)
        models_cfg = self._yaml_data.get("models", {})
        self.PRIMARY_MODEL = os.getenv("GEMINI_TEXT_MODEL") or models_cfg.get("primary", "gemini-3-flash-preview")
        self.BACKUP_MODEL = os.getenv("GEMINI_TEXT_BACKUP") or models_cfg.get("backup", "gemini-3.5-flash-lite")
        self.AUDIO_MODEL = os.getenv("GEMINI_AUDIO_MODEL") or models_cfg.get("audio", "gemini-3.5-transcribe")
        self.TEMPERATURE = models_cfg.get("temperature", 0.1)

        # Jira
        jira_cfg = self._yaml_data.get("jira", {})
        self.JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY") or jira_cfg.get("default_project_key", "KAN")
        self.JIRA_MAX_RESULTS = jira_cfg.get("max_results", 25)

        # GitHub
        github_cfg = self._yaml_data.get("github", {})
        self.GITHUB_PER_PAGE = github_cfg.get("per_page", 15)

        # Baseline Project
        proj_cfg = self._yaml_data.get("project_baseline", {})
        self.PROJECT_NAME = proj_cfg.get("name", "AI Project Manager Platform")
        self.AGREED_SCOPE: List[str] = proj_cfg.get("agreed_scope", [])
        self.TEAM_MEMBERS: List[str] = proj_cfg.get("team_members", [])

        # SQLite Database Path
        self.DB_PATH = ROOT_DIR / "data" / "agent.db"
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached singleton loader.
    Ensures .env and settings.yaml are read exactly once from disk.
    """
    return Settings()

# Direct importable singleton
settings = get_settings()
