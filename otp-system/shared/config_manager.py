# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Configuration Loader & Environment Handler
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# This module handles loading and validating the system configuration from external sources
# (YAML files, environment variables), with support for secrets management.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════

import os
import yaml
import logging
from typing import Dict, Any, Optional
from pathlib import Path
import re


logger = logging.getLogger(__name__)


class ConfigurationManager:
    """
    Manages loading and accessing system configuration.
    
    Supports:
    - YAML file loading with environment variable interpolation
    - Override via environment variables (APP_KEY__NESTED__PATH=value)
    - Secrets injection from Vault or environment
    """
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_path: Path to config.yaml. If None, searches standard locations.
        """
        self.config: Dict[str, Any] = {}
        self.config_path = config_path or self._find_config_file()
        self.secrets: Dict[str, str] = {}
        
        # Load configuration in order
        self._load_yaml_config()
        self._interpolate_env_vars()
        self._apply_env_overrides()
        
        logger.info(f"Configuration loaded from {self.config_path}")
    
    def _find_config_file(self) -> str:
        """Find config.yaml in standard locations."""
        search_paths = [
            Path("./config/config.yaml"),
            Path("/etc/otp-system/config.yaml"),
            Path(os.environ.get("OTP_CONFIG_PATH", "config/config.yaml")),
        ]
        
        for path in search_paths:
            if path.exists():
                logger.info(f"Found config at {path}")
                return str(path)
        
        raise FileNotFoundError("config.yaml not found in standard locations")
    
    def _load_yaml_config(self):
        """Load YAML configuration file."""
        try:
            with open(self.config_path, 'r') as f:
                self.config = yaml.safe_load(f) or {}
                logger.debug(f"Loaded YAML config with {len(self.config)} top-level keys")
        except Exception as e:
            logger.error(f"Failed to load YAML config: {e}")
            raise
    
    def _interpolate_env_vars(self):
        """
        Recursively interpolate environment variables in config values.
        Pattern: ${VAR_NAME} or ${VAR_NAME:default_value}
        """
        def replace_vars(obj: Any) -> Any:
            if isinstance(obj, str):
                # Replace ${VAR_NAME} with environment variable value
                def replace_match(match):
                    var_expr = match.group(1)  # e.g., "VAR_NAME" or "VAR_NAME:default"
                    
                    if ":" in var_expr:
                        var_name, default = var_expr.split(":", 1)
                    else:
                        var_name, default = var_expr, None
                    
                    value = os.environ.get(var_name, default)
                    if value is None:
                        logger.warning(f"Environment variable {var_name} not found, using empty string")
                        value = ""
                    return value
                
                return re.sub(r'\$\{([^}]+)\}', replace_match, obj)
            
            elif isinstance(obj, dict):
                return {k: replace_vars(v) for k, v in obj.items()}
            
            elif isinstance(obj, list):
                return [replace_vars(item) for item in obj]
            
            return obj
        
        self.config = replace_vars(self.config)
    
    def _apply_env_overrides(self):
        """
        Apply environment variable overrides in format:
        OTP_SYSTEM__LOG_LEVEL=DEBUG -> system.log_level = "DEBUG"
        OTP_REDIS__PRIMARY__POOL_SIZE=200 -> redis.primary.pool_size = 200
        """
        prefix = "OTP_"
        for env_var, value in os.environ.items():
            if not env_var.startswith(prefix):
                continue
            
            # Convert OTP_SYSTEM__LOG_LEVEL to ['system', 'log_level']
            key_path = env_var[len(prefix):].lower().split("__")
            
            # Try to parse as number or boolean
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            elif value.isdigit():
                value = int(value)
            
            # Navigate and set the value
            current = self.config
            for key in key_path[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            
            current[key_path[-1]] = value
            logger.debug(f"Applied override from env: {env_var} = {value}")
    
    def get(self, path: str, default: Any = None) -> Any:
        """
        Get config value by dot-separated path.
        
        Examples:
            config.get("redis.primary.urls")
            config.get("system.log_level", "INFO")
        """
        keys = path.split(".")
        value = self.config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def get_dict(self, path: str) -> Dict[str, Any]:
        """Get config section as dictionary."""
        value = self.get(path, {})
        if not isinstance(value, dict):
            raise ValueError(f"Config path {path} is not a dictionary")
        return value
    
    def get_list(self, path: str) -> list:
        """Get config section as list."""
        value = self.get(path, [])
        if not isinstance(value, list):
            raise ValueError(f"Config path {path} is not a list")
        return value
    
    def get_section(self, section: str) -> Dict[str, Any]:
        """Get entire configuration section."""
        if section in self.config:
            return self.config[section]
        return {}
    
    def validate_required_keys(self, section: str, required_keys: list) -> bool:
        """
        Validate that required configuration keys exist.
        
        Args:
            section: Config section name
            required_keys: List of required dot-separated keys
            
        Returns:
            True if all keys present, False otherwise
        """
        section_config = self.get_section(section)
        
        for key in required_keys:
            if self.get(f"{section}.{key}") is None:
                logger.error(f"Missing required config key: {section}.{key}")
                return False
        
        return True
    
    def log_config_summary(self):
        """Log a summary of loaded configuration (excluding secrets)."""
        logger.info("=== Configuration Summary ===")
        logger.info(f"System: {self.get('system.name')} v{self.get('system.version')}")
        logger.info(f"Environment: {self.get('system.environment')}")
        logger.info(f"Log Level: {self.get('system.log_level')}")
        logger.info(f"Redis Primary URLs: {len(self.get('redis.primary.urls', []))} nodes")
        logger.info(f"Message Broker: {self.get('message_broker.type')}")
        logger.info(f"Enabled Providers: {self._count_enabled_providers()}")
        logger.info(f"SLA Target: {self.get('sla.availability_target')}")
        logger.info("==============================")
    
    def _count_enabled_providers(self) -> int:
        """Count enabled message providers."""
        providers = self.get_dict("providers")
        return sum(1 for prov in providers.values() if isinstance(prov, dict) and prov.get("enabled", False))


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Logging Configuration
# ─────────────────────────────────────────────────────────────────────────────────────────────────

def setup_logging(config_manager: ConfigurationManager):
    """
    Configure structured logging based on configuration.
    """
    log_level = config_manager.get("system.log_level", "INFO")
    log_format = config_manager.get("observability.logging.format", "json")
    
    # Set root logger level
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # If JSON logging is enabled, use structured format
    if log_format == "json":
        import json_logging
        json_logging.init_non_web(enable_json=True)
    
    logger.info(f"Logging configured: level={log_level}, format={log_format}")


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Singleton Instance (Global Access)
# ─────────────────────────────────────────────────────────────────────────────────────────────────

_config_instance: Optional[ConfigurationManager] = None


def get_config() -> ConfigurationManager:
    """
    Get global configuration instance (singleton).
    Lazily initializes on first call.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigurationManager()
        setup_logging(_config_instance)
    return _config_instance


def initialize_config(config_path: Optional[str] = None) -> ConfigurationManager:
    """
    Explicitly initialize configuration with custom path.
    """
    global _config_instance
    _config_instance = ConfigurationManager(config_path)
    setup_logging(_config_instance)
    return _config_instance
