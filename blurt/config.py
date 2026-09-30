import json
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

type CleanupMode = Literal["openai", "basic", "off"]
type Reasoning = Literal["none", "low"]
type InjectMode = Literal["auto", "type", "paste"]
type BudgetAction = Literal["warn", "block"]

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blurt"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "blurt"
CONFIG_PATH = CONFIG_DIR / "config.toml"
CHOICES = {
    "cleanup": ("openai", "basic", "off"),
    "cleanup_reasoning": ("none", "low"),
    "inject": ("auto", "type", "paste"),
    "budget_action": ("warn", "block"),
}


@dataclass
class Config:
    transcribe_model: str = "gpt-transcribe"
    language: str = "en"
    vocabulary: list[str] = field(default_factory=list)
    cleanup: CleanupMode = "openai"
    cleanup_reasoning: Reasoning = "none"
    cleanup_instructions: str = ""
    cleanup_timeout: int = 25
    inject: InjectMode = "auto"
    type_delay_ms: int = 2
    paste_keys: str = "ctrl+v"
    trailing_space: bool = True
    monthly_budget: float = 10.0
    budget_action: BudgetAction = "warn"
    sounds: bool = True
    notifications: bool = True
    max_seconds: int = 300


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        save_config(Config())
    raw = tomllib.loads(CONFIG_PATH.read_text())
    defaults = Config()
    values: dict[str, Any] = {}
    for f in fields(Config):
        value = raw.get(f.name, getattr(defaults, f.name))
        default = getattr(defaults, f.name)
        values[f.name] = float(value) if isinstance(default, float) else value
    for key, allowed in CHOICES.items():
        if values[key] not in allowed:
            values[key] = getattr(defaults, key)
    return Config(**values)


def save_config(config: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Managed by `blurt settings`; edits here are picked up on restart."]
    for key, value in asdict(config).items():
        lines.append(f"{key} = {toml_value(value)}")
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(CONFIG_PATH)


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise TypeError(f"Unsupported config value: {value!r}")


def update_in_place(target: Config, source: Config) -> None:
    for f in fields(Config):
        setattr(target, f.name, getattr(source, f.name))
