from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    vault_dir: Path
    assistant_name: str
    token: str
    host: str
    port: int
    auto_memory_enabled: bool
    history_retrieval_mode: str
    semantic_history_url: str
    semantic_history_token: str
    semantic_history_timeout_seconds: float


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("SHIYUAN_DATA_DIR", "runtime-data")).resolve()
    assistant_name = os.environ.get("SHIYUAN_ASSISTANT_NAME", "我的助手").strip()
    if not assistant_name or len(assistant_name) > 32 or any(char in assistant_name for char in "\r\n"):
        raise RuntimeError("SHIYUAN_ASSISTANT_NAME must contain 1-32 characters on one line")
    token = os.environ.get("SHIYUAN_CORE_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("SHIYUAN_CORE_TOKEN must contain at least 32 characters")
    retrieval_mode = os.environ.get("SHIYUAN_HISTORY_RETRIEVAL_MODE", "keyword").strip().lower()
    if retrieval_mode not in {"keyword", "hybrid-shadow", "hybrid"}:
        raise RuntimeError("SHIYUAN_HISTORY_RETRIEVAL_MODE must be keyword, hybrid-shadow or hybrid")
    semantic_url = os.environ.get("SHIYUAN_SEMANTIC_HISTORY_URL", "").strip()
    if retrieval_mode != "keyword" and not semantic_url:
        raise RuntimeError("hybrid history retrieval requires SHIYUAN_SEMANTIC_HISTORY_URL")
    return Settings(
        data_dir=data_dir,
        db_path=data_dir / "shiyuan.sqlite3",
        vault_dir=data_dir / "vault",
        assistant_name=assistant_name,
        token=token,
        host=os.environ.get("SHIYUAN_HOST", "127.0.0.1"),
        port=int(os.environ.get("SHIYUAN_PORT", "8710")),
        auto_memory_enabled=os.environ.get("SHIYUAN_AUTO_MEMORY_ENABLED", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        history_retrieval_mode=retrieval_mode,
        semantic_history_url=semantic_url,
        semantic_history_token=os.environ.get("SHIYUAN_SEMANTIC_HISTORY_TOKEN", ""),
        semantic_history_timeout_seconds=max(
            0.2, min(float(os.environ.get("SHIYUAN_SEMANTIC_HISTORY_TIMEOUT_SECONDS", "3.0")), 30.0)
        ),
    )
