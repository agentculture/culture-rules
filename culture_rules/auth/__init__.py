"""Authentication and authorization for the HTTP API (standard-library only).

Every request resolves to a :class:`~culture_rules.auth.principal.Principal`
``{identity, kind, roles}`` before any handler runs, or is refused (401). Credentials:

- a Cloudflare Access JWT (``Cf-Access-Jwt-Assertion``), verified RS256 over the team JWKS
  with issuer, audience, ``exp`` and ``nbf`` checks (:mod:`.access`) - honoured on the
  loopback listener only, the one ``cloudflared`` forwards to;
- a service token (``Authorization: Bearer crt_...``), stored hashed in ``service_tokens``
  (:mod:`.tokens`) - accepted on both listeners, and the only credential on the LAN one.

Roles are ordered viewer < editor < admin; :mod:`.policy` maps each route to the role it
needs (403 otherwise). :mod:`.resolve` combines the two per listener.
"""
