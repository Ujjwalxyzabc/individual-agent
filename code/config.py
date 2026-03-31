
import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

class ConfigError(Exception):
    pass

class AgentConfig:
    """
    Configuration management for Shift Allowance Calculation Agent.
    Handles environment variables, API keys, LLM config, domain settings, and error handling.
    """

    # --- API Key Management ---
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    SHAREPOINT_CLIENT_ID = os.getenv("SHAREPOINT_CLIENT_ID")
    SHAREPOINT_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET")
    SHAREPOINT_TENANT_ID = os.getenv("SHAREPOINT_TENANT_ID")
    AUDIT_LOGGING_API_TOKEN = os.getenv("AUDIT_LOGGING_API_TOKEN")

    # --- LLM Configuration ---
    LLM_CONFIG = {
        "provider": "openai",
        "model": os.getenv("LLM_MODEL", "gpt-4o"),
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2000")),
        "system_prompt": (
            "You are a professional finance assistant specializing in shift allowance calculations. "
            "Your role is to securely access Excel files from SharePoint, extract shift details, and calculate allowances according to company policy. "
            "Always validate data, explain your calculations, and ensure compliance with security and privacy standards."
        ),
        "user_prompt_template": "Please provide the SharePoint URL and Excel file path for shift allowance calculation.",
        "few_shot_examples": [
            "Calculate shift allowance for file at https://company.sharepoint.com/sites/hr/shift_data.xlsx",
            "The file at the provided location is not accessible."
        ]
    }

    # --- Domain-specific Settings ---
    DOMAIN = "finance"
    AGENT_NAME = "Shift Allowance Calculation Agent"
    ALLOWED_FILE_EXTENSIONS = [".xlsx"]
    REQUIRED_CONFIG_KEYS = ["sharepoint_url", "excel_file_path"]
    SHIFT_RATE_TABLE = {
        "Night": 25,
        "Evening": 20,
        "Day": 15
    }
    # Compliance and Security
    COMPLIANCE = ["GDPR", "SOX"]
    SECURITY = {
        "auth": "OAuth2",
        "encryption": "AES-256",
        "pii_masking": True,
        "audit_logging": True
    }

    # --- API Endpoints & Requirements ---
    API_REQUIREMENTS = [
        {
            "name": "SharePoint REST API",
            "type": "external",
            "purpose": "Secure retrieval of Excel files from SharePoint",
            "authentication": "OAuth2",
            "rate_limits": "As per SharePoint API documentation"
        },
        {
            "name": "OpenAI API",
            "type": "external",
            "purpose": "LLM prompt processing and response generation",
            "authentication": "API Key",
            "rate_limits": "OpenAI standard limits"
        },
        {
            "name": "Audit Logging API",
            "type": "internal",
            "purpose": "Log access and calculation events for compliance",
            "authentication": "Service token",
            "rate_limits": "No explicit limit"
        }
    ]

    # --- Validation and Error Handling ---
    @classmethod
    def validate_env(cls):
        missing = []
        if not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if not cls.SHAREPOINT_CLIENT_ID:
            missing.append("SHAREPOINT_CLIENT_ID")
        if not cls.SHAREPOINT_CLIENT_SECRET:
            missing.append("SHAREPOINT_CLIENT_SECRET")
        if not cls.SHAREPOINT_TENANT_ID:
            missing.append("SHAREPOINT_TENANT_ID")
        if not cls.AUDIT_LOGGING_API_TOKEN:
            missing.append("AUDIT_LOGGING_API_TOKEN")
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

    @classmethod
    def get_openai_api_key(cls):
        if not cls.OPENAI_API_KEY:
            raise ConfigError("OPENAI_API_KEY is missing. Please set it in your environment.")
        return cls.OPENAI_API_KEY

    @classmethod
    def get_sharepoint_credentials(cls):
        if not (cls.SHAREPOINT_CLIENT_ID and cls.SHAREPOINT_CLIENT_SECRET and cls.SHAREPOINT_TENANT_ID):
            raise ConfigError("SharePoint credentials are missing. Please set SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET, and SHAREPOINT_TENANT_ID.")
        return {
            "client_id": cls.SHAREPOINT_CLIENT_ID,
            "client_secret": cls.SHAREPOINT_CLIENT_SECRET,
            "tenant_id": cls.SHAREPOINT_TENANT_ID
        }

    @classmethod
    def get_audit_logging_token(cls):
        if not cls.AUDIT_LOGGING_API_TOKEN:
            raise ConfigError("AUDIT_LOGGING_API_TOKEN is missing. Please set it in your environment.")
        return cls.AUDIT_LOGGING_API_TOKEN

    @classmethod
    def get_llm_config(cls):
        return cls.LLM_CONFIG

    @classmethod
    def get_domain_settings(cls):
        return {
            "domain": cls.DOMAIN,
            "agent_name": cls.AGENT_NAME,
            "allowed_file_extensions": cls.ALLOWED_FILE_EXTENSIONS,
            "required_config_keys": cls.REQUIRED_CONFIG_KEYS,
            "shift_rate_table": cls.SHIFT_RATE_TABLE,
            "compliance": cls.COMPLIANCE,
            "security": cls.SECURITY
        }

    @classmethod
    def get_api_requirements(cls):
        return cls.API_REQUIREMENTS

    # --- Default Values and Fallbacks ---
    @classmethod
    def get_default_llm_model(cls):
        return cls.LLM_CONFIG.get("model", "gpt-4o")

    @classmethod
    def get_fallback_llm_model(cls):
        return "gpt-3.5-turbo"

    @classmethod
    def get_system_prompt(cls):
        return cls.LLM_CONFIG.get("system_prompt")

    @classmethod
    def get_user_prompt_template(cls):
        return cls.LLM_CONFIG.get("user_prompt_template")

    @classmethod
    def get_few_shot_examples(cls):
        return cls.LLM_CONFIG.get("few_shot_examples", [])

# Validate configuration at import time
try:
    AgentConfig.validate_env()
except ConfigError as e:
    logging.error(str(e))
    raise

