"""Brevo 事务邮件发送。

只负责「把一封验证码邮件发出去」这一件事，限速和验证码生命周期由 website.py
的 email_codes 表管理。API key 走环境变量，没配置时 send_code_email() 返回
(False, 原因)，调用方据此提示用户而不是抛异常。
"""
from __future__ import annotations

import html
import logging
import os
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

# Brevo（原 Sendinblue）事务邮件接口
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY = os.environ.get('brevo_api_key', "")
BREVO_SENDER_EMAIL = os.environ.get("brevo_sender_email", "noreply@yitiao.org")
BREVO_SENDER_NAME = os.environ.get("brevo_sender_name", "Yitiao")

SITE_URL = "https://yitiao.org"
LOGO_URL = f"{SITE_URL}/static/logo_email.png"

_PLACEHOLDER_KEYS = ("", "REPLACE_WITH_BREVO_API_KEY")


def mailer_ready() -> bool:
    return BREVO_API_KEY not in _PLACEHOLDER_KEYS


def _html_body(code: str, action_text: str, expire_minutes: int) -> str:
    """深蓝黑底 + 顶部白色 LOGO + 大号验证码。全部内联样式，兼容邮件客户端。"""
    safe_action = html.escape(action_text)
    safe_code = html.escape(code)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Yitiao 验证码</title>
</head>
<body style="margin:0;padding:0;background-color:#02101f;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#02101f;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:520px;background-color:#04142d;border:1px solid rgba(255,255,255,0.10);
                      border-radius:20px;padding:36px 28px;">
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <img src="{LOGO_URL}" width="64" height="64" alt="Yitiao"
                   style="display:block;width:64px;height:64px;border:0;outline:none;">
            </td>
          </tr>
          <tr>
            <td align="center" style="font-family:'Helvetica Neue',Helvetica,Arial,'Microsoft YaHei',sans-serif;
                       font-size:20px;font-weight:bold;color:#eaf1ff;padding-bottom:14px;">
              邮箱验证码
            </td>
          </tr>
          <tr>
            <td align="center" style="font-family:'Helvetica Neue',Helvetica,Arial,'Microsoft YaHei',sans-serif;
                       font-size:15px;line-height:1.7;color:rgba(234,241,255,0.72);padding-bottom:26px;">
              你正在{safe_action}。<br>请在下方输入以下验证码完成验证。
            </td>
          </tr>
          <tr>
            <td align="center" style="padding-bottom:26px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center">
                <tr>
                  <td align="center" nowrap="nowrap"
                      style="white-space:nowrap;background-color:#10274e;
                             border:1px solid #3b70b3;border-radius:14px;
                             padding:16px 18px;font-family:'Courier New',Courier,monospace;
                             font-size:26px;font-weight:bold;letter-spacing:5px;color:#5aa7ff;">
                    {safe_code}
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td align="center" style="font-family:'Helvetica Neue',Helvetica,Arial,'Microsoft YaHei',sans-serif;
                       font-size:13px;line-height:1.7;color:rgba(234,241,255,0.46);padding-bottom:26px;">
              验证码 {expire_minutes} 分钟内有效，仅可使用一次。<br>
              如果这不是你本人的操作，请忽略这封邮件，并尽快检查账号安全。
            </td>
          </tr>
          <tr>
            <td align="center" style="border-top:1px solid rgba(255,255,255,0.08);padding-top:22px;">
              <a href="{SITE_URL}" style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                 font-size:13px;color:#5aa7ff;text-decoration:none;">yitiao.org</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def _text_body(code: str, action_text: str, expire_minutes: int) -> str:
    return (
        f"你正在{action_text}。\n\n"
        f"验证码：{code}\n\n"
        f"验证码 {expire_minutes} 分钟内有效，仅可使用一次。\n"
        f"如果这不是你本人的操作，请忽略这封邮件。\n\n{SITE_URL}\n"
    )


def send_code_email(to_email: str, code: str, action_text: str,
                    expire_minutes: int = 10) -> tuple[bool, str]:
    """发送验证码邮件。返回 (是否成功, 失败原因)。"""
    if not mailer_ready():
        return False, "邮件服务未配置"
    if not to_email:
        return False, "收件地址为空"

    payload = {
        "sender": {"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
        "to": [{"email": to_email}],
        "subject": f"Yitiao 验证码 {code}",
        "htmlContent": _html_body(code, action_text, expire_minutes),
        "textContent": _text_body(code, action_text, expire_minutes),
    }

    try:
        resp = requests.post(
            BREVO_API_URL,
            json=payload,
            headers={
                "api-key": BREVO_API_KEY,
                "content-type": "application/json",
                "accept": "application/json",
            },
            timeout=12,
        )
    except Exception as exc:
        logger.warning("Brevo email request failed: %s", type(exc).__name__)
        return False, f"邮件发送请求失败：{exc}"

    if resp.status_code in (200, 201, 202):
        return True, ""

    logger.warning("Brevo email rejected: HTTP %s", resp.status_code)

    detail = ""
    try:
        detail = str(resp.json().get("message") or "")
    except Exception:
        detail = resp.text[:200]
    return False, f"邮件发送失败（{resp.status_code}）{('：' + detail) if detail else ''}"
