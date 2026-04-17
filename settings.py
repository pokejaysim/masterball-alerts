#!/usr/bin/env python3
"""Shared paths and config-loading helpers for local overrides."""

import json
import os

MONITOR_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(MONITOR_DIR, "config.json")
CONFIG_LOCAL_FILE = os.path.join(MONITOR_DIR, "config.local.json")


def repo_path(*parts):
    return os.path.join(MONITOR_DIR, *parts)


def _load_json_file(path, default=None):
    if default is None:
        default = {}

    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _merge_values(base, override):
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _merge_values(merged[key], value)
            else:
                merged[key] = value
        return merged

    return override


def load_config():
    if not os.path.exists(CONFIG_FILE):
        raise FileNotFoundError(f"Config file not found at {CONFIG_FILE}")

    config = _load_json_file(CONFIG_FILE, {})
    if os.path.exists(CONFIG_LOCAL_FILE):
        config = _merge_values(config, _load_json_file(CONFIG_LOCAL_FILE, {}))
    return config


def load_json_with_local_override(filename, default=None):
    if default is None:
        default = {}

    base, ext = os.path.splitext(filename)
    local_path = repo_path(f"{base}.local{ext}")
    default_path = repo_path(filename)

    if os.path.exists(local_path):
        return _load_json_file(local_path, default)
    if os.path.exists(default_path):
        return _load_json_file(default_path, default)
    return default
