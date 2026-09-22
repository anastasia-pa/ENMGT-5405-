"""Productivity MCP server.

Runs over stdio, which is the transport MCP clients (Claude Desktop, IDE
extensions, the MCP Inspector) use to launch a local server as a subprocess.
"""

import os
import secrets
import time
import imaplib
import email 

from email.header import decode_header
from collections import deque

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

# Load .env before anything reads os.environ, so secrets are available at import time.
load_dotenv()

# The shared secret callers must present. Read once at startup; never logged or
# returned to the caller.
API_TOKEN = os.environ.get("API_TOKEN")

mcp = MCPServer(
    name="productivity",
    version="0.1.0",
    instructions="Tools for managing tasks and staying organized.",
)


# A single generic message for every authentication failure. Callers learn only
# that the token was rejected — not whether API_TOKEN is configured, how long it
# is, or where the server looked for it. Distinct messages per failure mode would
# be an information leak (error disclosure).
UNAUTHORIZED = "Unauthorized: Invalid token"


def _is_authorized(token: str) -> bool:
    """Check a caller-supplied token against the configured API_TOKEN.

    Returns False rather than raising, so an authentication failure can never
    surface a traceback (with its file paths and local variables) to the caller.

    Two deliberate choices here:

    1. An unset or empty API_TOKEN always fails. Otherwise a misconfigured server
       would silently accept `token=""` and every tool would be wide open.
    2. `secrets.compare_digest` instead of `==`. It compares in constant time, so
       the comparison's duration does not reveal how many leading characters the
       guess got right. Plain `==` exits at the first differing byte, which leaks
       that via timing.
    """
    if not API_TOKEN:
        return False
    return secrets.compare_digest(token, API_TOKEN)


# --- Rate limiting ---------------------------------------------------------
# Without a cap on attempts, nothing stops a caller from brute-forcing the
# token: the long random value is the only thing making that impractical, which
# is luck rather than a control. This limits every tool to 10 calls per rolling
# 60-second window.

RATE_LIMIT_MAX_CALLS = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0

RATE_LIMITED = "Rate limit exceeded. Please try again later."

# Timestamps of recent calls, oldest first. Shared across all tools and counted
# globally — NOT per token. An attacker brute-forcing sends a different token on
# every request, so per-token buckets would never fill up and the limit would be
# trivially bypassed.
_call_times: deque[float] = deque()


def _check_rate_limit() -> bool:
    """Record a call and report whether it is within the rate limit.

    Sliding window: drop timestamps that have aged out, then admit the call only
    if fewer than RATE_LIMIT_MAX_CALLS remain in the window.

    Uses `time.monotonic()`, not `time.time()`, because monotonic time cannot
    jump backwards — a system clock change or NTP correction can't be used to
    widen the window or flush the history.

    Note: this counter lives in the process, which is correct for stdio (the
    client launches one server subprocess and is its only caller). An HTTP
    transport would need per-client keys and shared state, since one process
    would serve many callers.
    """
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    while _call_times and _call_times[0] <= cutoff:
        _call_times.popleft()

    if len(_call_times) >= RATE_LIMIT_MAX_CALLS:
        return False

    _call_times.append(now)
    return True


@mcp.tool()
def get_upcoming_tasks(token: str) -> str:
   """Return recent Canvas notifications from your university."""
   if not _check_rate_limit():
        return RATE_LIMITED
   if not _is_authorized(token):
        return UNAUTHORIZED

   try:
        # 1. Connect to your email provider
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login("ap2597@cornell.edu", "hmuyjdpdaavkuufj")
        mail.select("inbox")

        # 2. Search only for Canvas emails
        _, canvas_data = mail.search(None, 'FROM', '"notifications@instructure.com"')
        email_ids = canvas_data[0].split()
        
        if not email_ids:
            return "No recent Canvas notifications found."

        # 3. Fetch and format the 5 most recent Canvas emails
        formatted_tasks = []
        for e_id in email_ids[-5:]: 
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8")
                    formatted_tasks.append(f"  Canvas Update: {subject}")

        mail.logout()
        return "Upcoming Tasks (Canvas):\n\n" + "\n".join(formatted_tasks)
   except Exception as e:
        return f"Error connecting to email server: {str(e)} "


@mcp.tool()
def flag_action_emails(token: str) -> str:
    """Return recent emails that have 'Action' in the subject line."""
    if not _check_rate_limit():
        return RATE_LIMITED
    if not _is_authorized(token):
        return UNAUTHORIZED

    import traceback
    try:
        # 1. Connect to your email provider
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login("ap2597@cornell.edu", "hmuyjdpdaavkuufj")
        mail.select("inbox")

        # 2. Search only for emails with "Action" in the subject
        _, action_data = mail.search(None, 'SUBJECT', '"action"')
        email_ids = action_data[0].split()
        
        if not email_ids:
            return "No recent action emails found."

        # 3. Fetch and format the 5 most recent Action emails
        formatted_emails = []
        for e_id in email_ids[-5:]:
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    # Safely handle emails without a subject line
                    raw_subject = msg.get("Subject")
                    if raw_subject:
                        subject, encoding = decode_header(raw_subject)[0]
                        if isinstance(subject, bytes):
                            subject = subject.decode(encoding if encoding else "utf-8")
                    else:
                        subject = "No Subject"
                        
                    sender = msg.get("From")
                    formatted_emails.append(f"  From: {sender}\n  Subject: {subject}")

        mail.logout()
        return "Emails Needing Action:\n\n" + "\n\n".join(formatted_emails)
    except Exception as e:
        # Force the exact error to appear in the Claude chat window
        return f"CRITICAL TOOL ERROR:\n{traceback.format_exc()}"

# --- Prompts ---------------------------------------------------------------
# A prompt is a reusable instruction template the user invokes deliberately
# (e.g. as a slash command in the client). Unlike a tool, the server does no
# work here: it hands back text that becomes the conversation's first message.
# `token` is a prompt argument the client will ask the user to fill in, which is
# how the model gets a value to pass to the two tools below.


@mcp.prompt()
def weekly_review() -> str:
    """A prompt template to generate an executive summary of tasks and emails."""
    return """Please write a professional executive summary for my week. 
First, use the get_upcoming_tasks tool to fetch my Canvas updates. 
Second, use the flag_action_emails tool to fetch my Action emails. 
Finally, synthesize all of the returned information into a clean, easy-to-read report. 
Do not invent tasks or emails to fill the gap. Use strictly the data the tools return."""


@mcp.resource("productivity://tasks")
def list_tasks() -> str:
    """The current task list, exposed as a readable resource."""
    return "No tasks yet."


if __name__ == "__main__":
    mcp.run(transport="stdio")