from __future__ import annotations

import json
import os
from urllib import request


def completion_payload(job: dict) -> dict:
    return {
        "status": job.get("status"),
        "output_name": job.get("output_name"),
        "error": job.get("error"),
        "file_count": job.get("file_count"),
    }


def slack_body(job: dict) -> dict:
    info = completion_payload(job)
    text = f"Quick Study {info['status']}: {info.get('output_name') or info.get('error') or '-'}"
    return {"text": text}


def feishu_body(job: dict) -> dict:
    info = completion_payload(job)
    return {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": f"Quick Study {info['status']}"}},
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**教程** {info.get('output_name') or '-'}\n"
                            f"**文件** {info.get('file_count') or '-'}\n"
                            f"**错误** {info.get('error') or '无'}"
                        ),
                    },
                }
            ],
        },
    }


def dingtalk_body(job: dict) -> dict:
    info = completion_payload(job)
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": f"Quick Study {info['status']}",
            "text": (
                f"### Quick Study {info['status']}\n\n"
                f"- 教程：{info.get('output_name') or '-'}\n"
                f"- 文件：{info.get('file_count') or '-'}\n"
                f"- 错误：{info.get('error') or '无'}"
            ),
        },
    }


def notify_completion(job: dict, *, timeout: float = 8) -> list[str]:
    sent = []
    slack = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()
    feishu = (os.getenv("FEISHU_WEBHOOK_URL") or "").strip()
    dingtalk = (os.getenv("DINGTALK_WEBHOOK_URL") or "").strip()
    extra = (os.getenv("COMPLETION_WEBHOOK_URL") or "").strip()
    targets = []
    if slack:
        targets.append(("slack", slack, slack_body(job)))
    if feishu:
        targets.append(("feishu", feishu, feishu_body(job)))
    if dingtalk:
        targets.append(("dingtalk", dingtalk, dingtalk_body(job)))
    if extra:
        targets.append(("generic", extra, completion_payload(job)))
    for name, url, body in targets:
        data = json.dumps(body).encode("utf-8")
        req = request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        sent.append(name)
    return sent
