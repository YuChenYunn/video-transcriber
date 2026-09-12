"""
每日转写文档邮件推送
====================
扫描 watch_dir 下新增/更新的 .txt/.md 文档，作为附件通过 QQ 邮箱发给你自己。
- 去重依据：mailer_state.json 记录每个文件上次发送时的 mtime；mtime 没变就不重发。
- 失败不更新状态，下次自动重试。
- 无新文档不发邮件。

用法：
    python daily_mailer.py                  # 正式运行（按状态过滤）
    python daily_mailer.py --test           # 忽略状态，强制发一封测试邮件（无附件），用来验证 SMTP 配置
    python daily_mailer.py --force-resend   # 忽略状态，把目录里所有文档重发一次（用于补发/归档）
"""

import json
import os
import smtplib
import sys
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "mailer_config.json"
STATE_PATH = APP_DIR / "mailer_state.json"
LOG_PATH = APP_DIR / "mailer.log"

TASK_NAME = "VideoTranscriberDailyMail"  # Windows 计划任务名，仅用于日志提示


def log(message):
    """同时输出到控制台和 mailer.log。"""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def load_config():
    if not CONFIG_PATH.exists():
        raise RuntimeError(f"找不到配置文件：{CONFIG_PATH}，请先按注释填写。")
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    # 过滤掉带下划线的说明字段。
    cfg = {k: v for k, v in raw.items() if not k.startswith("_")}
    missing = [k for k in ("smtp_user", "smtp_pass", "from_addr", "to_addr") if not cfg.get(k)]
    if missing:
        raise RuntimeError(f"配置不完整，请填写 mailer_config.json 中的：{', '.join(missing)}")
    if cfg["smtp_user"].strip().startswith("你的QQ邮箱") or "授权码" in cfg["smtp_pass"]:
        raise RuntimeError("mailer_config.json 还是占位符，请改成真实的邮箱地址和授权码后再运行。")
    return cfg


def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def collect_files(watch_dir, extensions):
    """返回 watch_dir 下匹配扩展名的文件列表（只看顶层，不递归子目录）。"""
    watch = Path(watch_dir)
    if not watch.exists():
        return []
    exts = {e.lower() for e in extensions}
    # .partial.txt 是转写程序的崩溃检查点（未完成稿），不要作为附件发送。
    return [p for p in watch.iterdir()
            if p.is_file() and p.suffix.lower() in exts
            and not p.name.endswith(".partial.txt")]


def find_new_files(files, state):
    """
    与 state（{文件名: mtime浮点数}）对比，返回新增/更新的文件。
    用文件名作 key（与现有转写程序一致：同目录同名即视为同一个文档）。
    """
    new_files = []
    for path in files:
        key = path.name
        mtime = path.stat().st_mtime
        if state.get(key) != mtime:
            new_files.append(path)
    return new_files


def build_attachments(file_paths):
    """把每个文件读成 MIMEBase 附件，中文文件名交给 email 库编码。"""
    attachments = []
    for path in file_paths:
        with open(path, "rb") as fh:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(fh.read())
        encoders.encode_base64(part)
        # Header 自动处理非 ASCII 文件名，避免乱码。
        part.add_header("Content-Disposition", "attachment", filename=path.name)
        attachments.append(part)
    return attachments


def send_message(cfg, subject, body_text, attachments=None):
    """用 SMTP SSL 发一封邮件。"""
    msg = MIMEMultipart()
    msg["From"] = cfg["from_addr"]
    msg["To"] = cfg["to_addr"]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    for part in attachments or []:
        msg.attach(part)

    host = cfg.get("smtp_host", "smtp.qq.com")
    port = int(cfg.get("smtp_port", 465))
    with smtplib.SMTP_SSL(host, port, timeout=30) as server:
        server.login(cfg["smtp_user"], cfg["smtp_pass"])
        server.sendmail(cfg["from_addr"], [cfg["to_addr"]], msg.as_string())


def run_test(cfg):
    """发一封无附件测试邮件，验证 SMTP 是否通。"""
    subject = f"[转写文档] 测试邮件 - {time.strftime('%Y-%m-%d %H:%M')}"
    body = "这是一封来自 daily_mailer.py 的测试邮件。如果你收到了，说明 SMTP 配置正确，每晚 9 点的定时任务可以正常发信。"
    send_message(cfg, subject, body)
    log("测试邮件已发送，请到收件箱确认。")


def run(cfg, force_resend=False):
    watch_dir = cfg["watch_dir"]
    files = collect_files(watch_dir, cfg.get("extensions", [".txt", ".md"]))
    state = load_state()

    # 先做一次状态收敛：记录下当前所有已知文件，删除已不存在文件的旧记录，避免 state 无限增长。
    current_names = {p.name for p in files}
    pruned = {k: v for k, v in state.items() if k in current_names}

    if force_resend:
        # --force-resend：忽略 state，把目录里所有匹配文件当作待发送，用于补发/归档。
        new_files = list(files)
        log(f"扫描 {watch_dir}：共 {len(files)} 个文档（强制重发模式，全部视为待发送）。")
    else:
        new_files = find_new_files(files, state)
        log(f"扫描 {watch_dir}：共 {len(files)} 个文档，其中 {len(new_files)} 个待发送。")

    if not new_files:
        log("没有新文档，跳过发送。")
        # 即使不发信，也把已删除文件的旧记录清掉，保持 state 干净。
        if len(pruned) != len(state):
            save_state(pruned)
        return

    # 一封邮件发完所有文档。这些转写稿都是纯文本/docx，体量很小（通常 <1MB），
    # 远低于邮箱附件上限，不需要分批。
    date_str = time.strftime("%Y-%m-%d")
    subject = f"[转写文档] {len(new_files)} 篇新文档 - {date_str}"
    listing = "\n".join(f"- {p.name}" for p in new_files)
    body = f"本次共发送 {len(new_files)} 篇转写文档（{date_str}）：\n\n{listing}\n\n附件见本邮件。"

    attachments = build_attachments(new_files)
    send_message(cfg, subject, body, attachments)
    log(f"已发送邮件《{subject}》，附件 {len(attachments)} 个。")

    # 发送成功才更新状态，失败时由异常处理保证不走到这里。
    for path in new_files:
        pruned[path.name] = path.stat().st_mtime
    save_state(pruned)


def main():
    is_test = "--test" in sys.argv
    is_force = "--force-resend" in sys.argv
    try:
        cfg = load_config()
        mode = "测试模式" if is_test else ("强制重发模式" if is_force else "正式模式")
        log(f"daily_mailer 启动（{mode}）。")
        if is_test:
            run_test(cfg)
        else:
            run(cfg, force_resend=is_force)
        log("完成。")
    except Exception as exc:
        # 任何异常都记日志，不抛出，避免 Windows 计划任务报错弹窗。
        log(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
