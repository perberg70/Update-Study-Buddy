"""Shared browser helpers for the NotebookLM automation scripts.

Centralizes the Chrome DevTools Protocol (CDP) connection so
export_current_sources.py, delete_agent.py and upload_agent.py agree on which
browser tab they drive.
"""

from __future__ import annotations

from config import CDP_URL

CHROME_HINT = "Start Chrome with: chrome.exe --remote-debugging-port=9222"

# Targets Chrome reports as pages but which cannot be navigated to a web URL.
NON_WEB_PREFIXES = ("chrome-extension://", "chrome://", "devtools://", "about:")


class BrowserConnectionError(RuntimeError):
    """Raised when no usable NotebookLM tab can be obtained over CDP."""


def is_web_page(page) -> bool:
    """True when *page* is a normal http(s) tab we are allowed to navigate."""
    try:
        url = page.url or ""
    except Exception:
        return False
    if url.startswith(NON_WEB_PREFIXES):
        return False
    return url.startswith(("http://", "https://"))


def pick_page(context, prefer: str = "notebooklm.google.com"):
    """Return a real web page from *context*, preferring one already on *prefer*.

    Chrome exposes extension offscreen documents, devtools windows and
    chrome:// pages as regular targets, and they are frequently listed first.
    Navigating one of those to an https URL fails with net::ERR_ABORTED, so
    they are skipped here rather than discovered as a crash later.
    """
    fallback = None
    for page in context.pages:
        if not is_web_page(page):
            continue
        if prefer and prefer in page.url:
            return page
        if fallback is None:
            fallback = page
    return fallback if fallback is not None else context.new_page()


def connect(playwright, prefer: str = "notebooklm.google.com"):
    """Connect to Chrome over CDP and return ``(browser, page)``.

    Raises BrowserConnectionError with an actionable message instead of
    surfacing a raw Playwright traceback or an IndexError.
    """
    try:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
    except Exception as exc:
        raise BrowserConnectionError(f"CDP connection failed: {exc}\n{CHROME_HINT}") from exc

    if not browser.contexts:
        raise BrowserConnectionError(
            f"Connected to {CDP_URL} but Chrome reported no browser contexts.\n{CHROME_HINT}"
        )

    return browser, pick_page(browser.contexts[0], prefer=prefer)


def describe_page(page) -> str:
    """Short label for logging which tab is being driven."""
    try:
        return page.url or "(new tab)"
    except Exception:
        return "(unknown tab)"
