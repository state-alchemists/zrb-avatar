"""Who may reach the face: a token in front of a server that is no longer only yours.

`zrb-avatar` starts on 127.0.0.1 and needs nothing, because a request that arrives there came
from this machine. The moment it is put behind `tailscale serve`, bound to a tailnet address, or
reached from a phone, that stops being true: anything that can reach the port could switch her
model, play a clip, put words in her mouth, or ask for a picture of the window. So a token guards
it, and a bind that is not loopback is refused without one -- the same shape as zrb's own web-bind
check (`src/zrb/runner/web_bind.py`), and for the same reason: a door that opens by itself is worse
than one that stays shut.

Three ways to present the token, because the callers differ:

* `Authorization: Bearer <token>` -- zrb's own client and this package's CLI, which are programs;
* the cookie a first visit with `?token=` sets -- a browser, which then needs nothing more, and
  whose own `EventSource` and `fetch` send it without any code of ours asking;
* `?token=<token>` -- a `curl`, and the one-shot link you paste into a phone.

Off unless `ZRB_AVATAR_TOKEN` is set, so nothing about a plain local run changes.
"""

import hmac
import ipaddress
import sys
import urllib.parse

# The cookie the page carries after one visit with `?token=`. Not `Secure`: the server speaks
# plain HTTP to whatever puts HTTPS in front of it (`tailscale serve` terminates TLS itself), so it
# cannot tell whether the browser arrived over https -- and a `Secure` cookie served over http is
# one the browser throws away. `HttpOnly` because the page reads nothing out of it.
COOKIE = "zrb_avatar_token"

# Literal IP addresses are parsed instead of listed, as zrb's web_bind does; an unknown name counts
# as exposed rather than as loopback.
_LOOPBACK_HOSTNAMES = {"localhost"}

# Enforced only for a bind that is not loopback. A six-character secret in front of an exposed port
# is a speed bump, not a lock.
MIN_TOKEN_LENGTH = 16


def is_loopback(host: str) -> bool:
    """True when *host* provably binds to loopback only; unknown names count as exposed."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() in _LOOPBACK_HOSTNAMES


def presented(headers, query: str, token: str) -> bool:
    """Whether this request carries *token*, in any of the three shapes.

    An empty *token* means the server wants none, so every request is allowed: that is what a
    plain local run is, and it must stay exactly what it was.
    """
    if not token:
        return True
    wanted = token.encode()
    for given in _offered(headers, query):
        if hmac.compare_digest(given.encode(), wanted):
            return True
    return False


def _offered(headers, query: str):
    """Every token this request presents, in the order the shapes are worth trusting.

    A header and a query value can both be there; either is enough, since each is the credential
    itself and none of them can be forged by a bystander.
    """
    yield from _from_authorization(headers.get("Authorization") or "")
    yield from _from_cookie(headers.get("Cookie") or "")
    for value in urllib.parse.parse_qs(query).get("token", []):
        yield value


def _from_authorization(header: str):
    """The token of `Bearer <token>`, the shape programs send."""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        yield value.strip()


def _from_cookie(header: str):
    """The token of the cookie a page carries after its first visit."""
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and value:
            yield urllib.parse.unquote(value)


def cookie_header(token: str) -> str:
    """The `Set-Cookie` value that gives the page its token back on every later request."""
    return f"{COOKIE}={urllib.parse.quote(token)}; Path=/; HttpOnly; SameSite=Lax"


def wants_a_cookie(target: str, token: str) -> bool:
    """Whether to trade a `?token=` for the cookie: only on the page itself, and only when right.

    Only the page, because a browser is the one caller that can make the trade; a `curl` asking
    `/models?token=...` for JSON is answered with the JSON rather than sent somewhere else with a
    cookie it will never use. A wrong token never reaches here -- the request is refused first.
    """
    if not token:
        return False
    url = urllib.parse.urlparse(target)
    return url.path in ("", "/") and "token" in urllib.parse.parse_qs(url.query)


def url_without_token(target: str) -> str:
    """*target*, with the `?token=` taken out and every other parameter left byte for byte.

    Byte for byte on purpose: `/?app&token=...` is the desktop window's own address, and a parse
    that renders `app` as `app=` -- or reorders what it found -- hands the window back a URL that
    is not quite the one it asked for.
    """
    url = urllib.parse.urlparse(target)
    kept = [part for part in url.query.split("&") if part and not _is_token(part)]
    return url.path + (f"?{'&'.join(kept)}" if kept else "")


def _is_token(part: str) -> bool:
    """Whether one `name=value` pair of a query string is the token."""
    return urllib.parse.unquote(part.partition("=")[0]) == "token"


def refuse_insecure_bind(host: str, token: str, variable: str) -> None:
    """Exit unless a bind that is not loopback is guarded by a token worth the name.

    There is deliberately no override flag, for the reason zrb's own check has none (ADR-0087):
    an override is the thing everyone reaches for once, and then a port that answers the tailnet
    is a setting somebody set on a laptop and forgot.
    """
    if is_loopback(host):
        return
    if not token.strip():
        _abort(
            f"Refusing to bind to '{host}' without a token ({variable} is empty): "
            "this would expose her window, her model and her animations to anyone "
            "who can reach this host.\n"
            f"  Fix: set {variable} to a secret of at least {MIN_TOKEN_LENGTH} "
            "characters,\n"
            "       or bind to 127.0.0.1 (the default) and reach her through "
            "`tailscale serve`, which needs no token of its own."
        )
    if len(token) < MIN_TOKEN_LENGTH:
        _abort(
            f"Refusing to bind to '{host}': {variable} is shorter than the "
            f"{MIN_TOKEN_LENGTH}-character minimum, which is a speed bump rather "
            "than a lock.\n"
            f"  Fix: set {variable} to a longer secret, or bind to 127.0.0.1 (the "
            "default)."
        )


def _abort(message: str) -> None:
    """Print a refusal and exit non-zero, so a service manager sees the failure."""
    print(f"\n{message}", file=sys.stderr)
    raise SystemExit(1)
