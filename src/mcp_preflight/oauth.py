"""OAuth metadata discovery, the way an MCP client finds the auth server.

Leena AI's Custom MCP docs say the connector "automatically discovers the
server's authorization and token endpoints from its published metadata", and
the admin only types a Client ID and Client Secret. So if discovery fails, the
connector can't finish the OAuth flow. This module walks the same lookups the
MCP auth spec describes (RFC 9728 then RFC 8414 / OIDC) and records each hop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .findings import Finding


@dataclass
class Discovery:
    steps: list[tuple[str, int | str]] = field(default_factory=list)  # (url, status or error)
    resource_metadata: dict | None = None
    auth_server: str | None = None
    as_metadata: dict | None = None
    source: str = ""  # "rfc9728" | "origin-fallback" | ""
    findings: list[Finding] = field(default_factory=list)


def resource_metadata_url(www_authenticate: str) -> str | None:
    m = re.search(r'resource_metadata="([^"]+)"', www_authenticate or "")
    return m.group(1) if m else None


def _origin_and_path(url: str) -> tuple[str, str]:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}", p.path.rstrip("/")


def _get_json(http: httpx.Client, url: str, d: Discovery) -> dict | None:
    try:
        r = http.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        d.steps.append((url, type(e).__name__))
        return None
    d.steps.append((url, r.status_code))
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _as_candidates(issuer: str) -> list[str]:
    origin, path = _origin_and_path(issuer)
    urls = []
    if path:
        urls += [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
    urls += [f"{origin}/.well-known/oauth-authorization-server", f"{origin}/.well-known/openid-configuration"]
    return urls


def discover(server_url: str, www_authenticate: str, http: httpx.Client) -> Discovery:
    d = Discovery()
    origin, path = _origin_and_path(server_url)

    # 1. Protected resource metadata (RFC 9728): header hint first, then well-known.
    prm_urls = []
    hint = resource_metadata_url(www_authenticate)
    if hint:
        prm_urls.append(hint)
    if path:
        prm_urls.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    prm_urls.append(f"{origin}/.well-known/oauth-protected-resource")
    for u in dict.fromkeys(prm_urls):
        prm = _get_json(http, u, d)
        if prm:
            d.resource_metadata = prm
            break

    if d.resource_metadata and d.resource_metadata.get("authorization_servers"):
        d.auth_server = d.resource_metadata["authorization_servers"][0]
        d.source = "rfc9728"
    else:
        # 2. Older servers (MCP spec 2025-03-26) put AS metadata on the server's own origin.
        d.auth_server = origin
        d.source = "origin-fallback"
        d.findings.append(Finding(
            "info", "A001", "oauth",
            "No RFC 9728 protected-resource metadata; only the older origin lookup (MCP spec 2025-03-26) "
            "finds the auth server. Leena's docs don't say which lookups the connector tries. "
            "Publishing /.well-known/oauth-protected-resource removes the guesswork.",
        ))

    for u in _as_candidates(d.auth_server):
        meta = _get_json(http, u, d)
        if meta:
            d.as_metadata = meta
            break

    if not d.as_metadata:
        d.source = ""
        d.findings.append(Finding(
            "error", "A002", "oauth",
            f"No authorization-server metadata found for {d.auth_server}. "
            "Leena can't discover the authorize/token endpoints, so OAuth setup will fail.",
        ))
        return d

    d.findings += check_as_metadata(d.as_metadata)
    return d


def check_as_metadata(meta: dict) -> list[Finding]:
    out: list[Finding] = []
    for key in ("authorization_endpoint", "token_endpoint"):
        if not meta.get(key):
            out.append(Finding("error", "A003", "oauth", f"Metadata has no {key}."))
    grants = meta.get("grant_types_supported", ["authorization_code", "implicit"])  # RFC 8414 default
    if "authorization_code" not in grants:
        out.append(Finding("error", "A004", "oauth",
                           "authorization_code grant not supported; Leena's connector uses that flow."))
    if "refresh_token" not in grants:
        out.append(Finding("warn", "A005", "oauth",
                           "refresh_token grant not advertised; tokens may expire and break the connection."))
    pkce = meta.get("code_challenge_methods_supported")
    if not pkce or "S256" not in pkce:
        out.append(Finding("warn", "A006", "oauth",
                           "S256 PKCE not advertised; the MCP auth spec requires PKCE with S256."))
    auth_methods = meta.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
    if not {"client_secret_basic", "client_secret_post"} & set(auth_methods):
        out.append(Finding("warn", "A007", "oauth",
                           f"Token endpoint accepts only {auth_methods}; Leena's form asks for a Client Secret."))
    if meta.get("registration_endpoint"):
        out.append(Finding("info", "A008", "oauth",
                           "Dynamic client registration exists, but Leena's form uses a pre-registered "
                           "Client ID/Secret. Register a client and add Leena's Redirect URI to it."))
    return out
