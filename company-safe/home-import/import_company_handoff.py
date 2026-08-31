from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any


def load_client(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not value.get("core_url") or not value.get("token"):
        raise ValueError("家庭十元客户端配置缺少 core_url/token")
    return value


def post(config: dict[str, Any], route: str, value: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        config["core_url"].rstrip("/") + route,
        data=json.dumps(value, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config['token']}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def validate(card: dict[str, Any]) -> None:
    if card.get("schema") != "shiyuan.company-handoff.v1":
        raise ValueError("不是受支持的十元公司交接卡")
    if card.get("export_status") != "awaiting_human_review":
        raise ValueError("该交接卡标为仅限公司本地，拒绝导入")
    if card.get("contains_company_confidential") or card.get("secret_like_pattern_detected"):
        raise ValueError("交接卡包含保密/疑似密钥标记，拒绝导入")
    if not str(card.get("title") or "").strip() or not str(card.get("summary") or "").strip():
        raise ValueError("交接卡缺少标题或摘要")


def main() -> None:
    parser = argparse.ArgumentParser(description="预览或导入经过人工审核的十元公司交接卡")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--approve-reviewed", action="store_true", help="确认已按公司制度人工审核")
    parser.add_argument("--create-task", action="store_true", help="有下一步时同时创建十元任务卡")
    parser.add_argument(
        "--client-config",
        type=Path,
        default=Path(os.environ.get("SHIYUAN_CLIENT_CONFIG", Path.home() / ".shiyuan" / "client.json")),
    )
    args = parser.parse_args()
    card = json.loads(args.file.read_text(encoding="utf-8"))
    validate(card)
    preview = {
        "id": card["id"],
        "title": card["title"],
        "summary": card["summary"],
        "decisions": card.get("decisions", []),
        "next_actions": card.get("next_actions", []),
        "source_body": card.get("source_body"),
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    if not args.approve_reviewed:
        print("\n仅预览，尚未写入十元 Core。确认合规后增加 --approve-reviewed。")
        return
    config = load_client(args.client_config)
    digest = hashlib.sha256(str(card["id"]).encode("utf-8")).hexdigest()
    event = post(
        config,
        "/v1/events",
        {
            "event_type": "company_handoff",
            "body": "company-safe-import",
            "device": "home-pc",
            "summary": card["summary"],
            "payload": {
                "handoff_id": card["id"],
                "title": card["title"],
                "decisions": card.get("decisions", []),
                "next_actions": card.get("next_actions", []),
                "source_body": card.get("source_body"),
                "human_reviewed": True,
            },
            "idempotency_key": f"company-handoff:{digest}",
        },
    )
    print(f"已写入十元事件：{event['id']}")
    if args.create_task and card.get("next_actions"):
        task = post(
            config,
            "/v1/tasks",
            {
                "title": card["title"],
                "objective": "\n".join(card["next_actions"]),
                "context": {
                    "company_handoff_id": card["id"],
                    "summary": card["summary"],
                    "decisions": card.get("decisions", []),
                },
                "acceptance": [],
                "source_body": "company-safe-import",
            },
        )
        print(f"已创建十元任务卡：{task['id']}")


if __name__ == "__main__":
    main()
