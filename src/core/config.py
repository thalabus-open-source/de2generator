"""Configuration management for de²generator."""

import copy
import json
import logging
import os
from typing import Optional


# Global configuration dictionary
_config = {}


def load_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from config.json file.
    
    Args:
        config_path: Path to config.json. If None, looks in parent directory of this module.
    
    Returns:
        Loaded configuration dictionary.
    """
    global _config
    
    if config_path is None:
        # Look for config.json in project root (two levels up from this file: core/config.py -> src/ -> project_root/)
        module_dir = os.path.dirname(__file__)  # src/core/
        src_dir = os.path.dirname(module_dir)   # src/
        project_root = os.path.dirname(src_dir)  # project_root/
        config_path = os.path.join(project_root, "config.json")
    
    try:
        with open(config_path) as f:
            _config = json.load(f)
            logging.info(f"✓ Loaded configuration from {config_path}")
            # Log translation settings for debugging
            translation_enabled = _config.get("translation", {}).get("enabled", False)
            debug_lang = _config.get("translation", {}).get("debug_force_language", "")
            logging.info(f"  → translation.enabled={translation_enabled}, debug_force_language={repr(debug_lang)}")
    except FileNotFoundError:
        logging.warning(f"Config file not found at {config_path}. Using hardcoded defaults.")
        _config = {}
    except Exception as e:
        logging.warning(f"Could not load config.json: {e}. Using hardcoded defaults.")
        _config = {}
    
    return _config


# Auto-load configuration when this module is imported so constants.py
# (which also imports from config.py) reads correct values at module init time.
load_config()


def get_config_value(key_path: str, env_var: Optional[str] = None, default=None):
    """Get config value from config.json, with env var and fallback support.
    
    Args:
        key_path: Dot-separated path to config value (e.g. "detection.repetition.window_tokens")
        env_var: Environment variable name to check first
        default: Default value if not found
    
    Returns:
        Configuration value with same type as default.
    """
    result = default
    
    # Try environment variable first
    if env_var and env_var in os.environ:
        val = os.environ[env_var]
        # Try to convert to appropriate type based on default
        try:
            if isinstance(default, bool):
                result = val.lower() in ('true', '1', 'yes')
            elif isinstance(default, int):
                result = int(val)
            elif isinstance(default, float):
                result = float(val)
            else:
                result = val
        except (ValueError, AttributeError):
            result = default
        return result
    
    # Try config.json
    keys = key_path.split(".")
    val = _config
    for key in keys:
        if isinstance(val, dict):
            val = val.get(key)
        else:
            val = None
            break
    
    if val is not None:
        # Type-cast the value to match default type
        try:
            if isinstance(default, bool):
                result = isinstance(val, bool) and val or False
            elif isinstance(default, int):
                result = int(val) if isinstance(val, (int, float)) else default
            elif isinstance(default, float):
                result = float(val) if isinstance(val, (int, float)) else default
            else:
                result = val
        except (ValueError, TypeError):
            result = default
    
    return result


def set_config_value(key_path: str, value) -> None:
    """Set a nested config key at runtime (dot-separated path), creating parents.

    Used by the UI endpoints that toggle features at runtime.  The change lives in
    memory only — config.json on disk is never rewritten.
    """
    keys = key_path.split(".")
    node = _config
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def delete_config_value(key_path: str) -> bool:
    """Remove a nested config key. Returns True when the key existed."""
    keys = key_path.split(".")
    node = _config
    for key in keys[:-1]:
        node = node.get(key)
        if not isinstance(node, dict):
            return False
    if keys[-1] in node:
        del node[keys[-1]]
        return True
    return False


def get_config_snapshot() -> dict:
    """Deep copy of the live configuration, for read-only endpoints and logging.

    Callers annotate the copy (the dashboard endpoint overlays feature flags), so
    a nested copy is required: a shallow one would hand out the live sub-dictionaries.
    """
    return copy.deepcopy(_config)


# Initialize configuration on module import
load_config()
