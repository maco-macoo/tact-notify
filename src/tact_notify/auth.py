"""Microsoft 365 SSO login to TACT via headless Playwright.

Returns the Sakai session cookies for httpx. Distinguishes failure kinds so the
caller can tell "wrong password" from "Microsoft risk challenge on this IP".
"""

from __future__ import annotations

import time
from pathlib import Path

import pyotp
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from .config import ARTIFACTS_DIR, BASE_URL, PORTAL_URL

LOGIN_URL = f"{PORTAL_URL}/login"

# Markers on login.microsoftonline.com pages
SEL_EMAIL = 'input[name="loginfmt"]'
SEL_PASSWORD = 'input[name="passwd"]'
SEL_SUBMIT = "#idSIButton9"
SEL_USERNAME_ERROR = "#usernameError"
SEL_PASSWORD_ERROR = "#passwordError"
# TOTP ("enter the code from your authenticator app") page
SEL_OTC_INPUT = '#idTxtBx_SAOTCC_OTC, input[name="otc"]'
SEL_OTC_SUBMIT = "#idSubmit_SAOTCC_Continue"
# "Sign in another way" proof picker: the verification-code option
SEL_PROOF_OTP_OPTION = '[data-value="PhoneAppOTP"]'
# Other verification challenge markers (push approval, CAPTCHA, proof pickers)
SEL_CHALLENGE = (
    "#idDiv_SAOTCS_Proofs, #idDiv_SAOTCC_Description, #idDiv_SAASTO_Description, "
    '#idDiv_SAASDS_Description, [data-testid="proofList"], iframe[src*="captcha"]'
)
SEL_KMSI_CHECKBOX = 'input[name="DontShowAgain"], #KmsiCheckboxField'
# THERS Shibboleth attribute-release consent page ("Accept" button)
SEL_SHIB_PROCEED = 'input[name="_eventId_proceed"]'


def totp_code(secret: str) -> str:
    return pyotp.TOTP(secret.replace(" ", "").upper()).now()


class LoginError(Exception):
    """kind: 'credentials' | 'challenge' | 'timeout' | 'unknown'"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _save_artifacts(page, label: str) -> None:
    try:
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS_DIR / f"{label}.png"), full_page=True)
        (ARTIFACTS_DIR / f"{label}.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass  # artifact capture must never mask the real error


def _attempt(pw, email: str, password: str, totp_secret: str, headless: bool) -> dict[str, str]:
    browser = pw.chromium.launch(headless=headless)
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)

        # Already inside the portal (e.g. persisted session) — nothing to do.
        if not page.url.startswith(BASE_URL):
            try:
                page.wait_for_selector(SEL_EMAIL, timeout=30_000)
            except PWTimeoutError:
                _save_artifacts(page, "login_no_email_form")
                raise LoginError("timeout", f"Microsoft email form did not appear (url={page.url})")
            page.fill(SEL_EMAIL, email)
            page.click(SEL_SUBMIT)

            try:
                page.wait_for_selector(f"{SEL_PASSWORD}:visible", timeout=30_000)
            except PWTimeoutError:
                if page.locator(SEL_USERNAME_ERROR).count():
                    raise LoginError("credentials", "Microsoft rejected the email (usernameError)")
                _save_artifacts(page, "login_no_password_form")
                raise LoginError("timeout", f"password form did not appear (url={page.url})")
            page.fill(SEL_PASSWORD, password)
            page.click(SEL_SUBMIT)

            # Post-password: TOTP prompt, KMSI prompt, challenge, error, or redirect to TACT.
            deadline = time.time() + 90
            otc_attempts = 0
            while time.time() < deadline:
                if page.url.startswith(BASE_URL):
                    break
                if page.locator(SEL_PASSWORD_ERROR).count():
                    raise LoginError("credentials", "Microsoft rejected the password (passwordError)")

                # If a method picker appears, choose "use a verification code".
                if totp_secret and page.locator(SEL_PROOF_OTP_OPTION).count():
                    try:
                        page.click(SEL_PROOF_OTP_OPTION, timeout=2_000)
                        page.wait_for_timeout(1_000)
                        continue
                    except Exception:
                        pass

                otc = page.locator(SEL_OTC_INPUT)
                if otc.count() and otc.first.is_visible():
                    if not totp_secret:
                        _save_artifacts(page, "login_challenge")
                        raise LoginError(
                            "challenge",
                            "TOTP code required but MS_TOTP_SECRET is not set",
                        )
                    if otc_attempts >= 2:
                        _save_artifacts(page, "login_totp_rejected")
                        raise LoginError(
                            "challenge",
                            "TOTP code rejected twice — MS_TOTP_SECRET is likely wrong",
                        )
                    otc.first.fill(totp_code(totp_secret))
                    otc_attempts += 1
                    try:
                        page.click(SEL_OTC_SUBMIT, timeout=2_000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2_000)
                    continue

                shib = page.locator(SEL_SHIB_PROCEED)
                if shib.count() and shib.first.is_visible():
                    try:
                        shib.first.click(timeout=2_000)  # attribute-release consent -> Accept
                        page.wait_for_timeout(1_000)
                        continue
                    except Exception:
                        pass

                if page.locator(SEL_CHALLENGE).count():
                    _save_artifacts(page, "login_challenge")
                    raise LoginError(
                        "challenge",
                        "Microsoft is asking for additional verification (MFA/risk challenge)",
                    )
                if page.locator(SEL_KMSI_CHECKBOX).count() and page.locator(SEL_SUBMIT).count():
                    try:
                        page.click(SEL_SUBMIT, timeout=2_000)  # "Stay signed in?" -> Yes
                    except Exception:
                        pass
                page.wait_for_timeout(1_000)
            else:
                _save_artifacts(page, "login_stuck")
                raise LoginError("timeout", f"never redirected back to TACT (url={page.url})")

        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45_000)
        cookies = {
            c["name"]: c["value"]
            for c in context.cookies(BASE_URL)
        }
        if not cookies:
            _save_artifacts(page, "login_no_cookies")
            raise LoginError("unknown", "reached portal but no cookies for TACT domain")
        return cookies
    finally:
        context.close()
        browser.close()


def login(
    email: str,
    password: str,
    totp_secret: str = "",
    headless: bool = True,
    attempts: int = 3,
) -> dict[str, str]:
    backoffs = [10, 30]
    last: LoginError | None = None
    with sync_playwright() as pw:
        for i in range(attempts):
            try:
                return _attempt(pw, email, password, totp_secret, headless)
            except LoginError as e:
                # Credential/challenge failures are deterministic — retrying just
                # hammers Microsoft and risks a lockout.
                if e.kind in ("credentials", "challenge"):
                    raise
                last = e
            except Exception as e:  # browser crash etc.
                last = LoginError("unknown", str(e))
            if i < attempts - 1:
                time.sleep(backoffs[min(i, len(backoffs) - 1)])
    assert last is not None
    raise last
