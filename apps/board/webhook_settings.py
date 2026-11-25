"""Private WeCom Webhook settings helper for 08:50 daily report.

Strict security rules:
- Never log or return the raw webhook key in GET responses.
- Restrict target URLs strictly to WeCom robot endpoints (prevents SSRF).
- Persist atomically with 0600 permissions inside a private directory.
"""
import datetime as dt
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

WECOM_PREFIX = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
WECOM_RE = re.compile(r"^https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]{10,128}$")
DEFAULT_SEND_TIME = "08:50"


def config_path() -> Path:
    return Path(os.environ.get("WEBHOOK_CONFIG_FILE", "/notify/daily-webhook.json"))


def validate_webhook_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("Webhook 地址必须是字符串")
    cleaned = url.strip()
    if not WECOM_RE.fullmatch(cleaned):
        raise ValueError("仅支持企业微信机器人地址（https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...）")
    return cleaned


def mask_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    cleaned = url.strip()
    if not cleaned.startswith(WECOM_PREFIX):
        return "已配置（格式待确认）"
    key = cleaned[len(WECOM_PREFIX):]
    suffix = key[-4:] if len(key) >= 4 else "****"
    return f"{WECOM_PREFIX}****{suffix}"


def _read_raw(path: Path) -> dict:
    if not path.exists() or path.is_symlink():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_settings(path: Path = None) -> dict:
    p = path or config_path()
    raw = _read_raw(p)
    url = str(raw.get("WebhookUrl") or "").strip()
    parent_writable = False
    try:
        parent_writable = p.parent.is_dir() and os.access(str(p.parent), os.W_OK)
    except OSError:
        parent_writable = False
    return {
        "enabled": bool(raw.get("Enabled", False)) if raw else False,
        "has_url": bool(url),
        "masked_url": mask_url(url),
        "send_time": str(raw.get("SendTime") or DEFAULT_SEND_TIME),
        "writable": parent_writable,
        "updated_at": raw.get("UpdatedAt"),
        "last_test": raw.get("LastTest"),
    }


def save_settings(payload: dict, path: Path = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    p = path or config_path()
    if p.is_symlink():
        raise ValueError("拒绝写入符号链接配置")
    if not p.parent.is_dir():
        raise ValueError("服务端未挂载推送配置目录，暂不可修改")

    existing = _read_raw(p)
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")

    new_url_raw = payload.get("webhook_url")
    if new_url_raw is not None and str(new_url_raw).strip() != "":
        url = validate_webhook_url(str(new_url_raw))
    else:
        url = str(existing.get("WebhookUrl") or "").strip()
        if enabled and not url:
            raise ValueError("启用推送前请先填写企业微信 Webhook 地址")
        if url:
            url = validate_webhook_url(url)

    now_iso = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    updated = {
        "Enabled": enabled,
        "WebhookUrl": url,
        "NotifyStart": False,
        "NotifySuccess": True,
        "SendTime": DEFAULT_SEND_TIME,
        "UpdatedAt": now_iso,
        "LastTest": existing.get("LastTest"),
    }
    tmp = p.with_suffix(".json.tmp")
    if tmp.is_symlink():
        raise ValueError("临时文件异常")
    data = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(str(tmp), 0o600)
    except OSError:
        pass
    os.replace(str(tmp), str(p))
    return read_settings(p)


def build_summary_text(board: dict, test_prefix: bool = False) -> str:
    s = board.get("summary") or {}
    assets = board.get("assets") or []
    total = int(s.get("total") or len(assets))
    delivered = int(s.get("delivered") or 0)
    pending = [a["name"] for a in assets if a.get("state") == "pending_verification"]
    attention = [a["name"] for a in assets if a.get("state") == "attention"]
    restored = [a["name"] for a in assets if (a.get("restore") or {}).get("status") == "passed"]
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    rate = f"{(delivered / total * 100):.1f}%" if total > 0 else "0.0%"
    title = f"【核心资产备份测试推送｜{stamp}】" if test_prefix else f"【核心资产备份日报｜{stamp}】"
    lines = [
        title,
        f"总体结果：成功到位 {delivered}/{total}（{rate}），失败/需处理 {len(attention)} 项",
        f"失败/需处理：{'、'.join(attention) if attention else '无'}",
        f"已到位待MD5复核：{len(pending)}项" + (f"（{'、'.join(pending)}）" if pending else ""),
        f"原生恢复通过：{'、'.join(restored) if restored else '尚无'}",
        "详情：http://192.168.1.212:8100/",
    ]
    return "\n".join(lines)


def send_test_message(board: dict, path: Path = None, opener=None) -> dict:
    p = path or config_path()
    raw = _read_raw(p)
    url = str(raw.get("WebhookUrl") or "").strip()
    if not url:
        raise ValueError("尚未保存 Webhook 地址，请先填写并保存")
    url = validate_webhook_url(url)
    content = build_summary_text(board, test_prefix=True)

    # When web container runs on an internal-only Docker network (no outbound DNS/internet),
    # delegate the HTTPS POST to the host-side systemd path worker via private /notify dir.
    if opener is None and os.environ.get("WEBHOOK_HOST_TRIGGER", "").lower() in ("1", "true", "yes"):
        req_id = secrets.token_hex(12)
        req_file = p.parent / "test-request.json"
        res_file = p.parent / "test-result.json"
        if req_file.is_symlink() or res_file.is_symlink():
            raise ValueError("测试触发文件路径异常")
        req_payload = {"request_id": req_id, "content": content}
        tmp = p.parent / "test-request.json.tmp"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(req_payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(str(tmp), str(req_file))

        deadline = time.monotonic() + 12.0
        result = None
        while time.monotonic() < deadline:
            res = _read_raw(res_file)
            if res.get("request_id") == req_id:
                result = res
                break
            time.sleep(0.2)
        if result is None:
            raise RuntimeError("宿主机推送服务未响应，请确认 core-backup-webhook-test.path 已启用")
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        ok = bool(result.get("ok"))
        errcode = result.get("errcode")
        errmsg = str(result.get("errmsg") or "")
        now_iso = str(result.get("sent_at") or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"))
        if p.parent.is_dir() and os.access(str(p.parent), os.W_OK) and not p.is_symlink():
            raw["LastTest"] = {"at": now_iso, "ok": ok, "errcode": errcode, "errmsg": errmsg[:120]}
            tmp2 = p.with_suffix(".json.tmp")
            fd = os.open(str(tmp2), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(str(tmp2), str(p))
        return {"ok": ok, "errcode": errcode, "errmsg": errmsg, "sent_at": now_iso}

    body = json.dumps({"msgtype": "text", "text": {"content": content}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    fn = opener or urllib.request.urlopen
    try:
        with fn(req, timeout=10) as resp:
            reply = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise RuntimeError(f"推送请求失败：{e}")

    errcode = reply.get("errcode")
    errmsg = str(reply.get("errmsg") or "")
    ok = errcode == 0
    now_iso = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    if p.parent.is_dir() and os.access(str(p.parent), os.W_OK) and not p.is_symlink():
        raw["LastTest"] = {"at": now_iso, "ok": ok, "errcode": errcode, "errmsg": errmsg[:120]}
        tmp = p.with_suffix(".json.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(str(tmp), str(p))
    return {"ok": ok, "errcode": errcode, "errmsg": errmsg, "sent_at": now_iso}
