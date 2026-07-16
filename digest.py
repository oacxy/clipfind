"""
Email digest — turns the cached Discover feed into an email that lands
in someone's inbox and pulls them back to the app to do the actual
clipping work. Pairs with the in-app Discover tab (same feed, same data,
two delivery surfaces).

Sends via Resend's HTTP API (https://resend.com) — a normal transactional
email provider, plain REST + API key, no SDK dependency needed since we
already have `requests`. Unsubscribe links are signed tokens (itsdangerous,
already a transitive Flask dependency) rather than a raw user ID in the
URL, so nobody can unsubscribe someone else just by guessing IDs.
"""

import os
from typing import List, Optional

import requests
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

RESEND_API_URL = "https://api.resend.com/emails"

UNSUBSCRIBE_SALT = "clipfind-unsubscribe"
# Tokens don't need to expire quickly — an unsubscribe link in an old
# email should still work months later. 400 days covers "basically never
# expires in practice" without literally being infinite.
UNSUBSCRIBE_MAX_AGE_SECONDS = 400 * 24 * 3600


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=UNSUBSCRIBE_SALT)


def generate_unsubscribe_token(user_id: int, secret_key: str) -> str:
    return _serializer(secret_key).dumps(user_id)


def verify_unsubscribe_token(token: str, secret_key: str) -> Optional[int]:
    try:
        return _serializer(secret_key).loads(token, max_age=UNSUBSCRIBE_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def render_digest_html(feed: List[dict], app_url: str, unsubscribe_url: str) -> str:
    """Builds the email body from the same feed dicts /api/discover
    serves — no separate data shape to keep in sync."""
    if not feed:
        return ""

    cards = []
    for pick in feed[:6]:
        clip = pick.get("clip", {})
        hook = clip.get("hook", "").strip()
        reasoning = clip.get("reasoning", "").strip()
        thumb = pick.get("thumbnail", "")
        title = pick.get("title", "Untitled video")
        channel = pick.get("channel_title", "")
        velocity = pick.get("velocity_score", 0)
        video_id = pick.get("video_id", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        cards.append(f"""
        <tr>
          <td style="padding:0 0 24px 0;">
            <table role="presentation" width="100%" style="background:#16161f;border:1px solid #26262f;border-radius:12px;overflow:hidden;">
              <tr>
                <td style="padding:0;">
                  <img src="{thumb}" width="100%" style="display:block;max-height:220px;object-fit:cover;" alt="">
                </td>
              </tr>
              <tr>
                <td style="padding:18px 20px;">
                  <div style="color:#9a9aa8;font-size:13px;margin-bottom:6px;">
                    {channel} &middot; <span style="color:#3ddc97;font-weight:600;">{velocity}x</span> normal velocity
                  </div>
                  <div style="color:#f2f2f5;font-size:17px;font-weight:700;margin-bottom:10px;">{title}</div>
                  <div style="color:#f2f2f5;font-size:14px;line-height:1.5;margin-bottom:8px;">
                    <strong>{hook}</strong>
                  </div>
                  <div style="color:#9a9aa8;font-size:13px;line-height:1.5;margin-bottom:16px;">
                    {reasoning}
                  </div>
                  <a href="{video_url}" style="color:#7c5cff;font-size:13px;text-decoration:none;">Watch source video &rarr;</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        """)

    return f"""
    <html>
    <body style="margin:0;padding:0;background:#0a0a0f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
      <table role="presentation" width="100%" style="max-width:600px;margin:0 auto;padding:32px 16px;">
        <tr>
          <td style="text-align:center;padding-bottom:8px;">
            <span style="font-weight:800;font-size:20px;color:#f2f2f5;">Clip<span style="color:#7c5cff;">Find</span></span>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;color:#9a9aa8;font-size:14px;padding-bottom:28px;">
            {len(feed)} videos outperforming right now — worth clipping today.
          </td>
        </tr>
        {''.join(cards)}
        <tr>
          <td style="text-align:center;padding:12px 0 24px 0;">
            <a href="{app_url}" style="display:inline-block;background:linear-gradient(135deg,#7c5cff,#ff5c9a);color:white;font-weight:600;font-size:14px;text-decoration:none;padding:14px 28px;border-radius:8px;">
              Open ClipFind &rarr;
            </a>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;color:#5a5a68;font-size:12px;padding-top:16px;">
            <a href="{unsubscribe_url}" style="color:#5a5a68;">Unsubscribe from this digest</a>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """


def send_email(to_email: str, subject: str, html: str, api_key: str, from_email: str) -> None:
    """Raises on failure — caller decides how to handle per-recipient errors."""
    resp = requests.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": from_email, "to": [to_email], "subject": subject, "html": html},
        timeout=20,
    )
    resp.raise_for_status()


def send_digest_emails(
    feed: List[dict],
    users: List,
    app_url: str,
    secret_key: str,
    api_key: str,
    from_email: str,
) -> dict:
    """Sends the digest to every opted-in user, one email each. Keeps
    going past individual failures (e.g. one bad address) rather than
    aborting the whole batch. Returns a summary dict for the caller to
    log/return in the cron response."""
    if not feed:
        return {"sent": 0, "failed": 0, "skipped_empty_feed": True}

    sent, failed = 0, []
    for user in users:
        if not user.email_opt_in:
            continue
        token = generate_unsubscribe_token(user.id, secret_key)
        unsubscribe_url = f"{app_url}/unsubscribe?token={token}"
        html = render_digest_html(feed, app_url, unsubscribe_url)
        try:
            send_email(
                to_email=user.email,
                subject=f"{len(feed)} videos worth clipping today",
                html=html,
                api_key=api_key,
                from_email=from_email,
            )
            sent += 1
        except Exception as e:
            print(f"[DIGEST] send failed for {user.email}: {e}", flush=True)
            failed.append(user.email)

    return {"sent": sent, "failed": len(failed), "failed_emails": failed}
