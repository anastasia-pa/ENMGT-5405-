# mcp-productivity

A local [Model Context Protocol](https://modelcontextprotocol.io) server exposing productivity
tools. It communicates over **stdio**, so an MCP client launches it as a subprocess.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Setup

The virtual environment is already created at `.venv`. To recreate it from scratch:

```powershell
uv venv
```

Activate it (Windows PowerShell):

```powershell
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script, allow it for the current session first:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

Install dependencies:

```powershell
uv pip install -r requirements.txt
```

## Configuration

Secrets live in `.env`, which is git-ignored. Fill in the value:

```
API_TOKEN=your-token-here
```

`server.py` loads it via `load_dotenv()` at import time. Generate a fresh token with:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Running

```powershell
uv run server.py
```

The server waits on stdin for a client to speak MCP to it — no output on startup is expected.

To test it interactively with the MCP Inspector:

```powershell
uv run mcp dev server.py
```

## What the server exposes

### Tools

Both tools require a valid `token` argument matching `API_TOKEN`, and share a rate limit of
10 calls per 60 seconds.

| Tool                 | Arguments      | Returns                                            |
| -------------------- | -------------- | -------------------------------------------------- |
| `get_upcoming_tasks` | `token: str`   | This week's university deliverables and quizzes    |
| `flag_action_emails` | `token: str`   | Recent emails needing action (sender, subject, deadline) |

Both return mock data — replace the bodies with a real calendar or inbox lookup.

### Prompts

| Prompt          | Arguments    | Purpose                                                    |
| --------------- | ------------ | ---------------------------------------------------------- |
| `weekly_review` | `token: str` | Instructs the model to call both tools and draft a summary email of deliverables and action items |

### Resources

| URI                     | Returns               |
| ----------------------- | --------------------- |
| `productivity://tasks`  | The current task list |

## Security

- **Token authentication.** Every tool takes a `token` argument checked against `API_TOKEN`
  by the shared `_is_authorized` helper. One helper for all tools, so they cannot drift apart
  in how strictly they check.
- **Constant-time comparison.** `secrets.compare_digest` rather than `==`, so the comparison's
  duration does not leak how many leading characters a guess got right.
- **Fails closed.** An unset or empty `API_TOKEN` rejects every request, including `token=""`.
  Otherwise a server whose `.env` failed to load would silently accept anything.
- **No error disclosure.** All authentication failures return the identical generic string
  `"Unauthorized: Invalid token"`. Returning rather than raising means no traceback — with its
  file paths and local variables — ever reaches the caller, and the message reveals nothing
  about whether a token is configured or how long it is.
- **Rate limiting.** 10 tool calls per rolling 60-second window, enforced by
  `_check_rate_limit()` *before* the auth check so failed guesses are throttled too. The
  counter is global rather than per-token, because a brute-force attempt sends a different
  token every request and per-token buckets would never fill. Uses `time.monotonic()`, so a
  clock change cannot widen the window.

Verify the rate limiter with the included exploit check:

```powershell
uv run exploit_check.py              # fast — simulated clock for window expiry
uv run exploit_check.py --real-wait  # also sleeps out a real 60s window
```

It fires 15 wrong-token guesses and asserts the first 10 return the auth error while the rest
are throttled, that alternating between tools cannot double the allowance, that a valid token
is also blocked once the window is spent (proving the limiter precedes auth), and that the
window genuinely expires. Exit code is 0 only if every check passes.

Known limitation: because the token is a per-call argument, it appears in tool inputs and in
the rendered `weekly_review` text, so it passes through the client's conversation history. A
production server would authenticate the transport instead, keeping the secret server-side.

## Project layout

| Path               | Purpose                                                 |
| ------------------ | ------------------------------------------------------- |
| `server.py`        | Server entry point; defines tools, prompts, resources   |
| `exploit_check.py` | Verifies the rate limiter blocks brute-force attempts   |
| `requirements.txt` | Pinned dependencies                                     |
| `.env`             | Local secrets (not committed)                           |
| `pyproject.toml`   | Project metadata for uv                                 |

## Adding a tool

Decorate a function in `server.py`. The type hints become the tool's input schema and the
docstring becomes its description, so both matter to the model calling it.

```python
@mcp.tool()
def my_tool(x: int) -> str:
    """What this tool does."""
    return str(x)
```
