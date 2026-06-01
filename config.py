from __future__ import annotations

"""
config.py — Load and merge per-module pipeline configuration.

Usage:
    cfg = load_config("StraParlaBO")        # merge defaults + module override
    cfg = load_config(None)                 # defaults only
    cfg = load_config("StraParlaBO", path)  # explicit configs directory
"""

from pathlib import Path
import yaml

_CONFIGS_DIR = Path(__file__).parent / "configs"


def _deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict that is *base* updated by *override*.

    - Dicts are merged recursively: only the keys present in *override* are
      changed; keys absent from *override* keep their *base* value.
    - All other types (lists, scalars) are replaced wholesale by the override
      value.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(module: str | None, configs_dir: Path | None = None) -> dict:
    """Load the merged configuration for *module*.

    Args:
        module:      module name (e.g. ``"StraParlaBO"``), or ``None`` for
                     defaults only.
        configs_dir: directory that contains ``defaults.yml`` and per-module
                     YAML files.  Defaults to the ``configs/`` directory next
                     to this file.

    Returns:
        A plain dict with all config keys resolved.

    Raises:
        FileNotFoundError: if ``defaults.yml`` or the module YAML is missing.
    """
    if configs_dir is None:
        configs_dir = _CONFIGS_DIR

    defaults_path = configs_dir / "defaults.yml"
    if not defaults_path.exists():
        raise FileNotFoundError(f"defaults.yml not found in {configs_dir}")

    with defaults_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if module is not None:
        module_path = configs_dir / f"{module}.yml"
        if not module_path.exists():
            raise FileNotFoundError(f"No config found for module '{module}' in {configs_dir}")
        with module_path.open(encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        config = _deep_merge(config, override)

    return config
