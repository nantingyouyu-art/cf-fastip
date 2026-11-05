#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare 电信优选 IP 采集器
数据源: https://api.uouin.com/cloudflare.html （每 10 分钟更新一次）

规则:
  1. 每 30 分钟抓一次，取电信(ctcc)排名第一（最快）的 IP
  2. 当天已记录过的 IP 自动去重，不重复写入
  3. 每 24 小时一个 txt，按日期分开存放：data/YYYY-MM-DD.txt
  4. 隔天把前一天 txt 邮件发送到指定邮箱
  5. txt 保留 3 天后自动删除
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import os
import re
import smtplib
import ssl
import sys
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 requests，请先执行: python -m pip install requests")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SECRETS_PATH = BASE_DIR / "secrets.json"
STATE_PATH = BASE_DIR / "state.json"

# 站点前端 JS 里硬编码的密钥种子（cloudflare.js 混淆后反解得出）
_SEED = "DdlTxtN0sUOu"
_SEED_MD5 = hashlib.md5(_SEED.encode()).hexdigest()
_KEY_MIDDLE = "70cloudflareapikey"
class SpeedBelowThreshold(RuntimeError):
    """首行速度不达标：属于正常的跳过，不是错误。"""


LINE_LABEL = {"ctcc": "电信", "cmcc": "移动", "cucc": "联通", "bgp": "三网", "ipv6": "IPv6"}
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|[0-9a-fA-F:]{4,}:\w[0-9a-fA-F:]*")

log = logging.getLogger("cfip")


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        sys.exit(f"配置文件不存在: {CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("data_dir", "data")
    cfg.setdefault("log_dir", "logs")
    cfg.setdefault("retention_days", 3)
    cfg.setdefault("line", "ctcc")
    return cfg


def setup_logging(cfg: dict[str, Any]) -> None:
    log_dir = BASE_DIR / cfg["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(log_dir / "cfip.log", maxBytes=2 * 1024 * 1024,
                            backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.handlers[:] = [fh, sh]
    log.setLevel(logging.INFO)


def load_state() -> dict[str, Any]:
    """state.json 只放「已发送日期」这类必须跟着仓库走的状态。

    上次运行时间 / 上次 IP 属于易变信息，写到 logs/last.json（被 gitignore），
    否则每次采集都会产生一次提交，把提交历史刷成垃圾。
    """
    if STATE_PATH.exists():
        try:
            with STATE_PATH.open(encoding="utf-8") as f:
                state = json.load(f)
            return {"sent": dict(state.get("sent") or {})}
        except Exception:
            log.warning("state.json 解析失败，重建")
    return {"sent": {}}


def save_state(state: dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"sent": state.get("sent", {})}, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_PATH)


def last_run_path() -> Path:
    d = BASE_DIR / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "last.json"


def save_last_run(entry: dict[str, Any]) -> None:
    try:
        last_run_path().write_text(
            json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # 只是展示用，失败不影响主流程
        log.debug("写 last.json 失败: %s", exc)


def load_last_run() -> dict[str, Any]:
    try:
        return json.loads(last_run_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def data_dir(cfg: dict[str, Any]) -> Path:
    d = BASE_DIR / cfg["data_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def day_file(cfg: dict[str, Any], d: date) -> Path:
    return data_dir(cfg) / f"{d.isoformat()}.txt"


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
def make_key(t_ms: int) -> str:
    """复刻站点前端的 key 算法（每次请求都不同，服务端据此校验）。"""
    return hashlib.md5(f"{_SEED_MD5}{_KEY_MIDDLE}{t_ms}".encode()).hexdigest()


def fetch_table(cfg: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    t_ms = int(time.time() * 1000)
    url = f"{cfg['endpoint']}?key={make_key(t_ms)}&time={t_ms}"
    sess = requests.Session()
    proxy = (cfg.get("proxy") or "").strip()
    if proxy:
        sess.trust_env = False
        sess.proxies = {"http": proxy, "https": proxy}
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Referer": "https://api.uouin.com/cloudflare.html",
        "X-Requested-With": "XMLHttpRequest",
    })
    resp = sess.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    # 该接口把数字字段序列化成字符串，"200" / 200 都要认
    if str(payload.get("code")) != "200" or not isinstance(payload.get("data"), dict):
        raise RuntimeError(f"接口返回异常: {resp.text[:200]}")
    return payload["data"]


def parse_speed_mbps(text: Any) -> float:
    """'60.07mb/s' -> 60.07；解析不出来返回 0.0（当作不达标处理）。"""
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(text or ""))
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def pick_best(cfg: dict[str, Any]) -> dict[str, Any]:
    """取指定线路（默认电信 ctcc）排名第一的 IP。

    速度门槛：首行速度必须严格大于 cfg["min_speed_mbps"]，否则本轮跳过，
    避免把龟速节点记进存档。阈值 <= 0 表示不限制。
    """
    line = cfg["line"]
    data = fetch_table(cfg)
    node = data.get(line) or {}
    rows = node.get("info") or []
    if not rows:
        raise RuntimeError(f"线路 {line} 无数据")
    best = rows[0]
    ip = str(best.get("ip", "")).strip()
    if not ip:
        raise RuntimeError("首行缺少 ip 字段")
    entry = {
        "ip": ip,
        "ping": str(best.get("ping", "")),
        "speed": str(best.get("speed", "")),
        "bandwidth": str(best.get("bandwidth", "")),
        "loss": str(best.get("loss", "")),
        "uptime": int(node.get("uptime") or 0),
        "speed_mbps": parse_speed_mbps(best.get("speed")),
    }
    floor = float(cfg.get("min_speed_mbps") or 0)
    if floor > 0 and entry["speed_mbps"] <= floor:
        raise SpeedBelowThreshold(
            f"首行 {ip} 速度 {entry['speed_mbps']:.2f}mb/s 未超过门槛 {floor:g}mb/s，本轮跳过")
    return entry


# --------------------------------------------------------------------------- #
# 存档
# --------------------------------------------------------------------------- #
def existing_ips(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ips: set[str] = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            for token in line.split():
                try:
                    ips.add(str(ipaddress.ip_address(token)))
                    break
                except ValueError:
                    continue
    return ips


def append_record(cfg: dict[str, Any], entry: dict[str, Any], now: datetime) -> bool:
    """写入当天文件；当天已存在该 IP 则跳过。返回是否新写入。"""
    path = day_file(cfg, now.date())
    seen = existing_ips(path)
    if entry["ip"] in seen:
        log.info("重复 IP 跳过: %s（当天已记录 %d 个唯一 IP）", entry["ip"], len(seen))
        return False

    new_file = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new_file:
            f.write(f"# Cloudflare 优选IP · {LINE_LABEL.get(cfg['line'], cfg['line'])} · {now.date().isoformat()}\n")
            f.write("# 采集间隔: %s 分钟 | 数据源: %s\n" % (cfg.get("interval_minutes", 30), cfg["endpoint"]))
            f.write("# 序号  采集时间              IP              延迟      速度        带宽\n")
        f.write("{:<5} {}  {:<15} {:<9} {:<10} {}\n".format(
            len(seen) + 1, now.strftime("%Y-%m-%d %H:%M:%S"), entry["ip"],
            entry["ping"], entry["speed"], entry["bandwidth"]))
    log.info("写入 %s -> %s %s", path.name, entry["ip"], entry["ping"])
    return True


# --------------------------------------------------------------------------- #
# 邮件
# --------------------------------------------------------------------------- #
def resolve_password(cfg: dict[str, Any]) -> str:
    mail = cfg.get("mail", {})
    if mail.get("password"):
        return str(mail["password"])
    env_name = mail.get("password_env") or "CFIP_SMTP_PASSWORD"
    if os.environ.get(env_name):
        return os.environ[env_name]
    if SECRETS_PATH.exists():
        try:
            with SECRETS_PATH.open(encoding="utf-8") as f:
                return str(json.load(f).get("smtp_password") or "")
        except Exception:
            pass
    return ""


def send_mail(cfg: dict[str, Any], day: date, path: Path, dry_run: bool = False) -> bool:
    mail = cfg.get("mail", {})
    if not mail.get("enabled", True):
        log.warning("邮件功能已禁用，跳过 %s", day)
        return False

    content = path.read_text(encoding="utf-8")
    lines = [l for l in content.splitlines() if l and not l.startswith("#")]
    subject = str(mail.get("subject", "CF优选IP {date}")).format(date=day.isoformat())
    body = (
        f"{day.isoformat()} 的 Cloudflare {LINE_LABEL.get(cfg['line'], cfg['line'])}优选 IP 汇总\n"
        f"采集方式: 每 {cfg.get('interval_minutes', 30)} 分钟取一次最快节点，当天去重后共 {len(lines)} 个唯一 IP\n"
        f"数据源: {cfg['endpoint']}（每 10 分钟更新）\n\n"
        f"{content}\n"
    )

    if dry_run:
        log.info("[dry-run] 邮件将发送至 %s，标题=%s，附件=%s（%d 行数据）",
                 mail.get("to"), subject, path.name, len(lines))
        return True

    password = resolve_password(cfg)
    if not password:
        log.error("缺少 SMTP 密码：请在 secrets.json 写入 {\"smtp_password\": \"应用专用密码\"} "
                  "或设置环境变量 %s", mail.get("password_env", "CFIP_SMTP_PASSWORD"))
        return False

    msg = EmailMessage()
    msg["From"] = mail.get("from") or mail.get("username")
    msg["To"] = ", ".join(mail.get("to") or [])
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(path.read_bytes(), maintype="text", subtype="plain", filename=path.name)

    host = mail.get("smtp_host", "smtp.gmail.com")
    port = int(mail.get("smtp_port", 465))
    context = ssl.create_default_context()
    try:
        if mail.get("use_ssl", True):
            with smtplib.SMTP_SSL(host, port, context=context, timeout=45) as server:
                server.login(mail["username"], password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=45) as server:
                server.starttls(context=context)
                server.login(mail["username"], password)
                server.send_message(msg)
    except Exception as exc:
        log.error("邮件发送失败: %s", exc)
        return False
    log.info("邮件已发送 -> %s（附件 %s）", msg["To"], path.name)
    return True


# --------------------------------------------------------------------------- #
# 生命周期
# --------------------------------------------------------------------------- #
def finalize_past_days(cfg: dict[str, Any], state: dict[str, Any], dry_run: bool = False) -> None:
    """把「不是今天」且未发送的存档邮件发出去。"""
    today = date.today()
    for path in sorted(data_dir(cfg).glob("*.txt")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day >= today or state["sent"].get(path.stem):
            continue
        if send_mail(cfg, day, path, dry_run=dry_run):
            if not dry_run:
                state["sent"][path.stem] = datetime.now().isoformat(timespec="seconds")
                save_state(state)


def purge_old(cfg: dict[str, Any], state: dict[str, Any], dry_run: bool = False) -> None:
    """删除超过保留天数的 txt（默认 3 天）。"""
    keep_days = int(cfg.get("retention_days", 3))
    cutoff = date.today() - timedelta(days=keep_days)
    for path in sorted(data_dir(cfg).glob("*.txt")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day > cutoff:
            continue
        if not state["sent"].get(path.stem) and not dry_run:
            log.warning("%s 超过保留期但尚未发送成功，保留一个周期后再删", path.name)
            continue
        if dry_run:
            log.info("[dry-run] 将删除 %s", path.name)
            continue
        path.unlink()
        state["sent"].pop(path.stem, None)
        log.info("已删除过期存档 %s（超过 %d 天）", path.name, keep_days)


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #
def cmd_collect(cfg: dict[str, Any], dry_run: bool = False) -> int:
    now = datetime.now()
    try:
        entry = pick_best(cfg)
    except SpeedBelowThreshold as exc:
        log.info("%s", exc)
        return 0
    except Exception as exc:
        log.error("采集失败: %s", exc)
        return 1
    if dry_run:
        log.info("[dry-run] 最优 %s IP = %s (%s, %s, %.2fmb/s) 门槛 %.0fmb/s",
                 LINE_LABEL.get(cfg["line"], cfg["line"]), entry["ip"], entry["ping"],
                 entry["speed"], entry["speed_mbps"], float(cfg.get("min_speed_mbps") or 0))
        return 0
    append_record(cfg, entry, now)
    save_last_run({
        "last_ip": entry["ip"],
        "last_ping": entry["ping"],
        "last_uptime": entry["uptime"],
        "last_run": now.isoformat(timespec="seconds"),
    })
    return 0


def cmd_run(cfg: dict[str, Any], dry_run: bool = False) -> int:
    rc = cmd_collect(cfg, dry_run=dry_run)
    state = load_state()
    finalize_past_days(cfg, state, dry_run=dry_run)
    purge_old(cfg, state, dry_run=dry_run)
    return rc


def cmd_status(cfg: dict[str, Any]) -> int:
    state = load_state()
    last = load_last_run()
    print(f"数据源     : {cfg['endpoint']}")
    print(f"线路       : {cfg['line']} ({LINE_LABEL.get(cfg['line'], '?')})")
    print(f"采集间隔   : {cfg.get('interval_minutes', 30)} 分钟")
    print(f"保留天数   : {cfg.get('retention_days', 3)} 天")
    print(f"速度门槛   : > {cfg.get('min_speed_mbps', 0):g} mb/s（首行不达标则跳过本轮）")
    print(f"收件人     : {', '.join(cfg.get('mail', {}).get('to') or [])}")
    print(f"上次运行   : {last.get('last_run', '-')}")
    print(f"上次 IP    : {last.get('last_ip', '-')}  {last.get('last_ping', '')}")
    print("存档:")
    for path in sorted(data_dir(cfg).glob("*.txt")):
        ips = existing_ips(path)
        flag = "已发送" if state["sent"].get(path.stem) else "待发送"
        print(f"  {path.name}  {len(ips):>3} 个唯一 IP  {flag}")
    return 0


def cmd_send(cfg: dict[str, Any], which: str, dry_run: bool = False) -> int:
    state = load_state()
    if which == "all":
        finalize_past_days(cfg, state, dry_run=dry_run)
        return 0
    path = day_file(cfg, date.fromisoformat(which))
    if not path.exists():
        log.error("存档不存在: %s", path)
        return 1
    ok = send_mail(cfg, date.fromisoformat(which), path, dry_run=dry_run)
    if ok and not dry_run:
        state["sent"][path.stem] = datetime.now().isoformat(timespec="seconds")
        save_state(state)
    return 0 if ok else 1


def cmd_test_mail(cfg: dict[str, Any]) -> int:
    mail = cfg.get("mail", {})
    password = resolve_password(cfg)
    if not password:
        print("未找到 SMTP 密码（secrets.json 或环境变量 %s）" % mail.get("password_env"))
        return 1
    msg = EmailMessage()
    msg["From"] = mail.get("from") or mail.get("username")
    msg["To"] = ", ".join(mail.get("to") or [])
    msg["Subject"] = "CFIP 测试邮件"
    msg.set_content("这是一封连通性测试邮件，收到即表示 SMTP 配置正确。")
    context = ssl.create_default_context()
    try:
        if mail.get("use_ssl", True):
            with smtplib.SMTP_SSL(mail.get("smtp_host", "smtp.gmail.com"),
                                  int(mail.get("smtp_port", 465)), context=context, timeout=45) as s:
                s.login(mail["username"], password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(mail.get("smtp_host", "smtp.gmail.com"),
                              int(mail.get("smtp_port", 587)), timeout=45) as s:
                s.starttls(context=context)
                s.login(mail["username"], password)
                s.send_message(msg)
    except Exception as exc:
        print("发送失败:", exc)
        return 1
    print("测试邮件已发送 ->", msg["To"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloudflare 电信优选 IP 采集器")
    parser.add_argument("--dry-run", action="store_true", help="只演示，不写文件/不发邮件")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("collect", help="抓一次最快 IP 并追加到当天存档")
    sub.add_parser("run", help="采集 + 发送到期存档 + 清理过期（计划任务用这个）")
    sub.add_parser("status", help="查看当前状态")
    sub.add_parser("test-mail", help="发送一封测试邮件")
    p_send = sub.add_parser("send", help="立即发送某天的存档，all=所有待发送")
    p_send.add_argument("date", help="YYYY-MM-DD 或 all")
    p_purge = sub.add_parser("purge", help="按保留天数清理存档")

    args = parser.parse_args(argv)
    cfg = load_config()
    setup_logging(cfg)

    if args.cmd in (None, "run"):
        return cmd_run(cfg, dry_run=args.dry_run)
    if args.cmd == "collect":
        return cmd_collect(cfg, dry_run=args.dry_run)
    if args.cmd == "status":
        return cmd_status(cfg)
    if args.cmd == "test-mail":
        return cmd_test_mail(cfg)
    if args.cmd == "send":
        return cmd_send(cfg, args.date, dry_run=args.dry_run)
    if args.cmd == "purge":
        purge_old(cfg, load_state(), dry_run=args.dry_run)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())