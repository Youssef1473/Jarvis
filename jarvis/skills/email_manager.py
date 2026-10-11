"""Email integration for Gmail and Outlook via secure IMAP."""
import imaplib
import email
from email.header import decode_header
import os
import logging
from typing import List, Dict, Any, Optional
from jarvis.skills.base import SkillRegistry

logger = logging.getLogger("Jarvis.Email")


def _decode_str(header_val: Any) -> str:
    """Decodes email header values properly."""
    if not header_val:
        return ""
    decoded_fragments = decode_header(header_val)
    parts = []
    for fragment, charset in decoded_fragments:
        if isinstance(fragment, bytes):
            try:
                parts.append(fragment.decode(charset or "utf-8", errors="replace"))
            except Exception:
                parts.append(fragment.decode("latin-1", errors="replace"))
        else:
            parts.append(str(fragment))
    return "".join(parts).strip()


def _get_email_body(msg) -> str:
    """Extracts plain text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")
    # Clean whitespace and return snippet
    cleaned = " ".join([line.strip() for line in body.splitlines() if line.strip()])
    return cleaned[:300]


def _fetch_unread_imap(host: str, username: str, password: str, max_count: int = 5) -> List[Dict[str, str]]:
    """Fetches unread emails from an IMAP server."""
    if not username or not password:
        return []

    mail = imaplib.IMAP4_SSL(host)
    try:
        mail.login(username, password)
        mail.select("INBOX", readonly=True)

        status, messages = mail.search(None, "UNSEEN")
        if status != "OK" or not messages[0]:
            return []

        email_ids = messages[0].split()
        latest_ids = email_ids[-max_count:][::-1]  # Latest first

        results = []
        for eid in latest_ids:
            status, data = mail.fetch(eid, "(RFC822)")
            if status != "OK" or not data or not data[0]:
                continue
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = _decode_str(msg.get("Subject", "No Subject"))
            from_addr = _decode_str(msg.get("From", "Unknown Sender"))
            date_str = msg.get("Date", "")
            snippet = _get_email_body(msg)

            results.append({
                "id": eid.decode("utf-8", errors="ignore"),
                "from": from_addr,
                "subject": subject,
                "date": date_str,
                "snippet": snippet
            })
        return results
    finally:
        try:
            mail.logout()
        except Exception:
            pass


@SkillRegistry.register(
    name="check_unread_emails",
    description="Checks and summarizes unread emails from your Gmail and/or Outlook inboxes."
)
def check_unread_emails(account: str = "all", max_emails: int = 5) -> str:
    """Checks unread emails from configured Gmail and Outlook accounts."""
    account = account.lower().strip()
    summaries = []

    # 1. Gmail
    if account in ("all", "gmail", "google"):
        gmail_user = os.getenv("GMAIL_ADDRESS", "").strip()
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "").strip()
        if not gmail_user or not gmail_pass:
            if account in ("gmail", "google"):
                return "Gmail is not configured yet, Sir. Please add GMAIL_ADDRESS and GMAIL_APP_PASSWORD to your .env file."
        else:
            try:
                gmail_msgs = _fetch_unread_imap("imap.gmail.com", gmail_user, gmail_pass, max_emails)
                if gmail_msgs:
                    items = [f"- From: {m['from']}\n  Subject: {m['subject']}\n  Preview: {m['snippet'][:120]}..." for m in gmail_msgs]
                    summaries.append(f"Gmail ({len(gmail_msgs)} unread):\n" + "\n".join(items))
                else:
                    summaries.append("Gmail: Inbox is clear, no unread messages.")
            except Exception as e:
                summaries.append(f"Gmail connection error: {e}")

    # 2. Outlook
    if account in ("all", "outlook", "microsoft", "hotmail", "office365"):
        outlook_user = os.getenv("OUTLOOK_ADDRESS", "").strip()
        outlook_pass = os.getenv("OUTLOOK_APP_PASSWORD", "").strip()
        if not outlook_user or not outlook_pass:
            if account in ("outlook", "microsoft", "hotmail", "office365"):
                return "Outlook is not configured yet, Sir. Please add OUTLOOK_ADDRESS and OUTLOOK_APP_PASSWORD to your .env file."
        else:
            try:
                outlook_msgs = _fetch_unread_imap("outlook.office365.com", outlook_user, outlook_pass, max_emails)
                if outlook_msgs:
                    items = [f"- From: {m['from']}\n  Subject: {m['subject']}\n  Preview: {m['snippet'][:120]}..." for m in outlook_msgs]
                    summaries.append(f"Outlook ({len(outlook_msgs)} unread):\n" + "\n".join(items))
                else:
                    summaries.append("Outlook: Inbox is clear, no unread messages.")
            except Exception as e:
                summaries.append(f"Outlook connection error: {e}")

    if not summaries:
        return (
            "Neither Gmail nor Outlook credentials have been configured yet, Sir.\n"
            "To enable email checking, add your credentials in the .env file:\n"
            "- GMAIL_ADDRESS and GMAIL_APP_PASSWORD\n"
            "- OUTLOOK_ADDRESS and OUTLOOK_APP_PASSWORD"
        )

    return "\n\n".join(summaries)


def get_unread_email_count() -> int:
    """Returns the total number of unread emails across configured accounts."""
    count = 0
    gmail_user = os.getenv("GMAIL_ADDRESS", "").strip()
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if gmail_user and gmail_pass:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(gmail_user, gmail_pass)
            mail.select("INBOX", readonly=True)
            status, messages = mail.search(None, "UNSEEN")
            if status == "OK" and messages and messages[0]:
                count += len(messages[0].split())
            mail.logout()
        except Exception:
            pass
    return count
