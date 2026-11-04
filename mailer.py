"""mailer.py — sending email, and staying quiet when it cannot.

DORMANT UNTIL CONFIGURED, the same shape as billing.py. With no RESEND_API_KEY this module
logs what it would have sent and returns False. Nothing upstream branches on that: a
password-reset request answers the same either way (see the enumeration note in api.py),
and a "your report is ready" that could not be sent must never fail the run that produced
the report. An unconfigured instance is a working instance with no email, not a broken one.

NO SDK, for the reason auth.py and billing.py give: one HTTP POST against `requests`, which
is already a dependency, beats a package and its supply chain.

WHY EMAIL AT ALL. Two things in this product cannot exist without it. A forgotten password
is an unrecoverable account today — sessions are stateless 30-day HMACs with no server-side
record, so there is nothing to reset against and no address we have proven we can reach.
And a report takes about six minutes, which is longer than anyone watches a tab.

PROVIDER-AGNOSTIC ON PURPOSE. Resend is the default because its API is one POST, but the
only provider-specific thing here is _send_resend; MAIL_PROVIDER picks another.
"""
from __future__ import annotations

import os
from logger import get

log = get("mailer")

RESEND_API = "https://api.resend.com/emails"

#: Where mail comes from. A sending domain you control and have verified with the
#: provider; anything else lands in spam or is refused outright.
FROM_ENV = "MAIL_FROM"
KEY_ENV = "RESEND_API_KEY"


class MailError(Exception):
    """Operator-facing. Never raised into a request path."""


def configured() -> bool:
    """The key, the sender, AND the public URL.

    Every message this module sends carries a link. Without CASTOR_PUBLIC_URL the reset
    and confirmation mails ship the literal string "(no public URL configured)" where the
    link belongs, and "your report is ready" ships an empty one. That is worse than sending
    nothing: the operator pastes a Resend key, the flow reports success, and account
    recovery is silently dead on a deploy that looks complete.
    """
    return bool((os.environ.get(KEY_ENV) or "").strip()
                and (os.environ.get(FROM_ENV) or "").strip()
                and base_url())


def sender() -> str:
    return (os.environ.get(FROM_ENV) or "").strip()


def base_url() -> str:
    """The origin to build links against.

    A reset link is useless if it points at localhost, and the request that triggers it may
    arrive through a tunnel or a proxy whose Host header we do not trust for this. So the
    operator states it once, and it is required for any mail that carries a link.
    """
    return (os.environ.get("CASTOR_PUBLIC_URL") or "").strip().rstrip("/")


def send(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one message. True if the provider accepted it.

    NEVER RAISES. Every caller is in a path where failing to send is worth a log line and
    nothing more: the account was still created, the report was still produced, and the
    reset request must answer identically whether or not the address exists.
    """
    to = (to or "").strip()
    if not to:
        return False
    if not configured():
        log.info("[mail] not configured — would have sent %r to %s", subject, _mask(to))
        return False
    try:
        import requests
        r = requests.post(
            RESEND_API, timeout=15,
            headers={"Authorization": f"Bearer {os.environ[KEY_ENV].strip()}",
                     "Content-Type": "application/json"},
            json={"from": sender(), "to": [to], "subject": subject,
                  "text": text, **({"html": html} if html else {})})
        if r.status_code >= 400:
            # The body can echo the address; log the status and the provider's code only.
            log.warning("[mail] provider refused %r for %s: HTTP %s",
                        subject, _mask(to), r.status_code)
            return False
        return True
    except Exception as e:                                   # noqa: BLE001
        log.warning("[mail] send failed for %s: %s", _mask(to), type(e).__name__)
        return False


def _mask(email: str) -> str:
    """Logs are read by more people than the mailbox is. Keep enough to debug with."""
    name, _, domain = (email or "").partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


# ------------------------------------------------------------------------- messages --
def send_password_reset(to: str, token: str) -> bool:
    url = f"{base_url()}/reset?token={token}" if base_url() else "(no public URL configured)"
    return send(
        to, "Reset your Castor password",
        "Someone asked to reset the password for this Castor Research account.\n\n"
        f"{url}\n\n"
        "The link works once and expires in an hour. If it wasn't you, nothing has "
        "changed and you can ignore this.\n")


def send_verify_email(to: str, token: str) -> bool:
    url = f"{base_url()}/verify?token={token}" if base_url() else "(no public URL configured)"
    return send(
        to, "Confirm your email for Castor",
        "Confirm this address so you can recover your account later.\n\n"
        f"{url}\n\nThe link expires in 24 hours.\n")


def _guest_trailer(to: str) -> str:
    """What a guest needs to know that an account holder does not.

    THE LINK IS OWNED BY A COOKIE. A guest's report belongs to the guest id in the browser
    that bought it, so the link opens there and nowhere else. Telling them it was "in your
    library" was the account holder's truth mailed to someone with no library. Confirming
    the address is what moves the report (api._claim_prepaid), so the mail says exactly
    that, with the address spelled out because it has to be this one.
    """
    where = f" at {base_url()}/login" if base_url() else ""
    return (f"This link opens on the browser that bought the report. To read it anywhere "
            f"else, create an account with {to}{where} and confirm the address when the "
            f"confirmation mail arrives: the report then moves to that account's library, "
            f"on any device.\n")


def send_report_ready(to: str, job_id: str, name: str = "", guest: bool = False) -> bool:
    url = f"{base_url()}/jobs/{job_id}/report.html" if base_url() else ""
    what = f"Your report on {name} is ready" if name else "Your Castor report is ready"
    trailer = (_guest_trailer(to) if guest
               else "It is in your library whenever you want it.\n")
    return send(to, what, f"{what}.\n\n{url}\n\n{trailer}")


def send_report_withheld(to: str, job_id: str, name: str = "",
                         guest: bool = False) -> bool:
    """A withheld report is still news, and silence reads as a failed purchase."""
    url = f"{base_url()}/jobs/{job_id}/report.html" if base_url() else ""
    what = f"Your report on {name} needs a look" if name else "Your Castor report needs a look"
    trailer = f"\n{_guest_trailer(to)}" if guest else ""
    return send(to, what,
                f"{what}.\n\nThe run finished, but its own checks flagged something and it "
                f"is being held back rather than published as-is. The page explains which "
                f"check and what would clear it.\n\n{url}\n{trailer}")


def send_coupon(to: str, code: str, value_usd: float) -> bool:
    """The thank-you for putting a report in the library.

    Sent only when there is no account to store it on. The code is on their screen at the
    same moment; this is what makes it survive them closing the tab, which is the whole
    reason we asked for an address.
    """
    amount = f"${value_usd:.0f}"
    where = f"\n\nStart one here: {base_url()}/survey" if base_url() else ""
    return send(to, f"Your {amount} Castor credit: {code}",
                f"Thank you for sharing your report.\n\n"
                f"Your code is {code}. It takes {amount} off your next report. Enter it "
                f"in the discount box at checkout.{where}\n\n"
                f"It is good for one report. If you make an account with this address, "
                f"the code follows you onto it.\n")
