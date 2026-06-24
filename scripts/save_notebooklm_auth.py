#!/usr/bin/env python3
"""
Fallback when `notebooklm login` crashes on "Navigation interrupted".

Uses the same browser profile as notebooklm login. If you already signed in
during a failed login attempt, this script saves storage_state.json without
the post-ENTER cookie-sync navigation that triggers the Playwright bug.

Usage:
  python scripts/save_notebooklm_auth.py
  notebooklm auth check --test
"""

from __future__ import annotations

import sys
from pathlib import Path

NOTEBOOKLM_URL = "https://notebooklm.google.com/"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
        from notebooklm.paths import get_browser_profile_dir, get_storage_path
    except ImportError:
        print("Install: pip install notebooklm-py[browser] && playwright install chromium", file=sys.stderr)
        return 1

    profile = get_browser_profile_dir()
    storage = get_storage_path()
    storage.parent.mkdir(parents=True, exist_ok=True)

    print(f"Profile: {profile}")
    print(f"Saving to: {storage}")
    print("Opening browser... (close manually if already on NotebookLM home)")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--password-store=basic"],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(NOTEBOOKLM_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            if "interrupted" not in str(exc).lower():
                raise
            print(f"Note: redirect during load ({exc.__class__.__name__}), continuing...")

        input("\nWhen NotebookLM homepage is visible, press ENTER to save auth... ")

        if "notebooklm.google.com" not in page.url:
            print(f"Warning: URL is {page.url}", file=sys.stderr)
            if input("Save anyway? [y/N] ").strip().lower() != "y":
                context.close()
                return 1

        context.storage_state(path=str(storage))
        storage.chmod(0o600)
        context.close()

    print(f"Saved: {storage}")
    print("Verify: notebooklm auth check --test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
