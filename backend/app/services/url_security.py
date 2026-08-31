"""Validazione degli endpoint HTTP usati dal backend."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException


def validate_endpoint(value: str, *, allow_local: bool = True) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="endpoint non valido: usa un URL http(s) senza credenziali")
    host = parsed.hostname.rstrip(".").lower()
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise HTTPException(status_code=400, detail="endpoint non risolvibile") from exc
    local_host = host in {"localhost", "127.0.0.1", "::1"} or all(ipaddress.ip_address(a).is_loopback for a in addresses)
    if local_host and allow_local:
        return value.strip().rstrip("/")
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="gli endpoint remoti devono usare HTTPS")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise HTTPException(status_code=400, detail="endpoint verso rete privata o metadata non consentito")
    return value.strip().rstrip("/")
