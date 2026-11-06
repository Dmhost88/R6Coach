"""Configuration loading: config.yaml (settings) and .env (secrets)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class CaptureCfg(BaseModel):
    window_title: str = "Rainbow Six"
    sample_fps: float = 2.0
    upload_width: int = 1600


class VisionCfg(BaseModel):
    model: str = "claude-opus-5-5"
    effort: str = "low"
    # With the local HUD reader on, live rounds rarely need cloud reads.
    min_interval: dict[str, float] = Field(
        default_factory=lambda: {
            "default": 6,
            "operator_select": 3,
            "map_ban": 4,
            "operator_ban": 4,
            "prep_phase": 12,
            "action_phase": 90,
            "spectating": 60,
            "round_end": 4,
        }
    )

    def interval_for(self, screen: str) -> float:
        return float(self.min_interval.get(screen, self.min_interval.get("default", 6)))


class HudCfg(BaseModel):
    enabled: bool = True
    period: float = 1.0          # seconds between local HUD reads


class CoachCfg(BaseModel):
    model: str = "claude-opus-5-5"
    # Measured on Opus 5.5 with cached prompts: low ~9 s to the first spoken line, medium ~19 s.
    effort: str = "low"              # bans, questions, live prep (short windows: must be quick)
    pick_effort: str = "medium"      # operator pick (op select gives ~30 s)
    deep_effort: str = "high"        # defense prep plan (made during pick) and round debriefs
    live_effort: str = "low"         # in-round tactical calls (must land within seconds)
    live_min_gap: float = 15.0       # seconds between in-round tactical calls
    stream: bool = True              # speak the first line while the rest is still being written
    budget_per_match: float = 3.0    # USD; at 80% live calls and debriefs pause, at 100% only bans/picks
    skill_level: str = "intermediate"
    chattiness: dict[str, int] = Field(
        default_factory=lambda: {
            "map_ban": 1,
            "operator_ban": 1,
            "operator_select": 2,
            "prep_phase": 2,
            "action_phase": 1,
            "between_rounds": 1,
        }
    )

    def level(self, phase: str) -> int:
        return int(self.chattiness.get(phase, 1))


class OperatorPool(BaseModel):
    attack: list[str] = Field(default_factory=list)
    defense: list[str] = Field(default_factory=list)


class VoiceCfg(BaseModel):
    # piper (local neural voice, no key, default) | kokoro (local, more natural, slower) |
    # openai | elevenlabs | sapi (robotic Windows voice).
    # Whatever is chosen, failures fall back to the local piper voice, then sapi.
    provider: str = "piper"
    local_voice: str = "en_GB-alan-medium"   # Piper voice, see `python -m r6coach.voice.tts --voices`
    kokoro_voice: str = "am_michael"
    kokoro_model: str = "kokoro-v1.0.onnx"   # fp32; the int8 file is ~7x slower on CPU
    local_device: str = "cpu"                # cpu | cuda (needs onnxruntime-gpu; cpu leaves the GPU to the game)
    local_threads: int = 4                   # CPU threads for the local voice (0 = onnxruntime default)
    speed: float = 1.05
    openai_voice: str = "onyx"
    openai_model: str = "gpt-4o-mini-tts"
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_flash_v2_5"
    volume: float = 0.9
    output_device: Optional[str] = None


class PttCfg(BaseModel):
    key: str = "f13"
    whisper_model: str = "base.en"
    whisper_device: str = "cuda"


class DashboardCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True


class StorageCfg(BaseModel):
    data_dir: str = "data"
    keep_frames_days: int = 14

    def path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else ROOT / p


class Config(BaseModel):
    player_name: str = ""  # your in-game name, so the coach can tell your kills apart
    capture: CaptureCfg = Field(default_factory=CaptureCfg)
    vision: VisionCfg = Field(default_factory=VisionCfg)
    hud: HudCfg = Field(default_factory=HudCfg)
    coach: CoachCfg = Field(default_factory=CoachCfg)
    operator_pool: OperatorPool = Field(default_factory=OperatorPool)
    not_owned: list[str] = Field(default_factory=list)
    voice: VoiceCfg = Field(default_factory=VoiceCfg)
    push_to_talk: PttCfg = Field(default_factory=PttCfg)
    dashboard: DashboardCfg = Field(default_factory=DashboardCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)


def load_env(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; existing environment variables win."""
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if value and key not in os.environ:
                os.environ[key] = value


def config_path() -> Path:
    return ROOT / "config.yaml"


def load_config(path: Optional[Path] = None) -> Config:
    load_env(ROOT / ".env")
    path = path or config_path()
    if not path.exists():
        path = ROOT / "config.example.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Config.model_validate(data)


def save_config(cfg: Config, path: Optional[Path] = None) -> None:
    path = path or config_path()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(cfg.model_dump(), f, sort_keys=False, allow_unicode=True)
