# Server deployment

This page sets up Tabularium on a Linux server (a VPS or a lab machine) for a team:
installed with uv for a dedicated service user, run by systemd, and served over HTTPS
on a whole domain by a reverse proxy. It was tested end to end on Ubuntu 26.04 with
Caddy, behind Cloudflare: login, upload, OCR, the live channel and backup.

Replace `tabularium.example.org` with your domain. The commands run as root.

## 1. Install for a service user

By default `uv tool install` puts the tool, and the Python it downloads, under
`~/.local/share/uv` of whoever runs it. That is usually root's home, mode `700`, which
a service user cannot read. Put both in shared folders instead:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # if uv is not installed yet

export UV_TOOL_DIR=/opt/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin UV_PYTHON_INSTALL_DIR=/opt/uv/python
uv tool install --python 3.13 tabularium

useradd --system --home-dir /var/lib/tabularium --create-home --shell /usr/sbin/nologin tabularium
```

`--python 3.13` matters: without it uv uses the system Python, which is 3.14 on recent
distributions and is not supported ([Installation](installation.md)). Keep the same
three variables for every later `uv tool` command, for example the upgrade:

```bash
UV_TOOL_DIR=/opt/uv/tools UV_TOOL_BIN_DIR=/usr/local/bin UV_PYTHON_INSTALL_DIR=/opt/uv/python \
  uv tool upgrade --python 3.13 tabularium
```

The data goes in `/var/lib/tabularium/data`, owned by the service user. Tabularium
creates it on the first start, with the `.env` that holds the vault key.

## 2. The systemd unit

`/etc/systemd/system/tabularium.service`:

```ini
[Unit]
Description=Tabularium
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tabularium
Group=tabularium
WorkingDirectory=/var/lib/tabularium
Environment=HOME=/var/lib/tabularium
Environment=TABULARIUM_ROOT=/var/lib/tabularium/data
Environment=TABULARIUM_HOST=127.0.0.1
Environment=TABULARIUM_PORT=8787
Environment=TABULARIUM_PUBLIC_URL=https://tabularium.example.org
Environment=TABULARIUM_SESSION_COOKIE_SECURE=1
Environment=TABULARIUM_FORWARDED_ALLOW_IPS=127.0.0.1,::1
ExecStart=/usr/local/bin/tabularium --no-browser
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

The app listens on loopback only; the proxy is the way in. The variables that matter
in production, all described in [Configuration](configuration.md):

| Variable | Value here | Why |
|---|---|---|
| `TABULARIUM_PUBLIC_URL` | `https://tabularium.example.org` | the address users type: OIDC callbacks, and the setup link in the log |
| `TABULARIUM_SESSION_COOKIE_SECURE` | `1` | the session cookie is sent over HTTPS only |
| `TABULARIUM_FORWARDED_ALLOW_IPS` | `127.0.0.1,::1` | trust `X-Forwarded-For` from the local proxy; add the CDN's ranges behind a CDN ([below](#behind-cloudflare-or-another-cdn)) |

Other settings can also go in `/var/lib/tabularium/data/.env`; variables set in the
unit win over the file.

Do not start the service yet: read step 4 first.

## 3. The reverse proxy

The proxy terminates TLS, allows large uploads (whole PDFs, image folders) and passes
WebSockets through: the live channel (`/api/live/...`) shows who is on a page.
ZIP archives are uploaded in 16 MB pieces, so they also pass a CDN that caps request
bodies (Cloudflare's free plan refuses bodies over 100 MB).

### Caddy

Caddy gets and renews the certificate by itself. `/etc/caddy/Caddyfile`:

```
tabularium.example.org {
	encode zstd gzip
	request_body {
		max_size 500MB
	}
	reverse_proxy 127.0.0.1:8787
}
```

Caddy passes WebSockets with no extra configuration. Then `systemctl reload caddy`.

### nginx

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name tabularium.example.org;

    ssl_certificate     /etc/letsencrypt/live/tabularium.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tabularium.example.org/privkey.pem;

    client_max_body_size 500m;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        $connection_upgrade;
        proxy_read_timeout 1h;
    }
}

server {
    listen 80;
    listen [::]:80;
    server_name tabularium.example.org;
    return 301 https://$host$request_uri;
}
```

`$connection_upgrade` needs this once, in the `http` block:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
```

Get the certificate with certbot (`certbot --nginx -d tabularium.example.org`), then
`nginx -t && systemctl reload nginx`.

### Behind Cloudflare or another CDN

With a CDN in front of the proxy, the address the app sees is the CDN edge, unless it
trusts the CDN's ranges as well. The login rate limit is keyed on that address, so
this matters: [Configuration](configuration.md#behind-cloudflare-or-another-cdn)
explains it and builds the list for Cloudflare. Put the ranges in
`TABULARIUM_FORWARDED_ALLOW_IPS` in the unit (CIDR ranges are accepted).

Caddy must trust them too, or it replaces the client address with the edge's. On
Caddy 2.6 (the Ubuntu package), `trusted_proxies` goes inside `reverse_proxy`; the
global `servers { trusted_proxies ... }` option exists only from 2.7:

```
	reverse_proxy 127.0.0.1:8787 {
		trusted_proxies 173.245.48.0/20 103.21.244.0/22 ...
	}
```

With nginx, use `set_real_ip_from` for each range and `real_ip_header CF-Connecting-IP`.

## 4. Start, and create the administrator

Until the first account exists, setup makes its creator the administrator. It needs
the one-time setup token, so nobody who just finds the URL can claim the instance
([First administrator](installation.md#first-administrator)).

```bash
systemctl daemon-reload
systemctl enable --now tabularium
journalctl -u tabularium | grep "setup token"
```

The log line includes the link to open, `https://tabularium.example.org/?setup_token=...`;
the token is also in `/var/lib/tabularium/data/setup-token`. Open the link, create the
administrator, and the token file disappears.

## 5. Check the service with `doctor`

A plain `tabularium doctor` checks the data folder of the user who runs it
(`~/Tabularium`), not the service's. Run it as the service user, with the service's
environment:

```bash
sudo -u tabularium env HOME=/var/lib/tabularium TABULARIUM_ROOT=/var/lib/tabularium/data \
  TABULARIUM_SESSION_COOKIE_SECURE=1 TABULARIUM_FORWARDED_ALLOW_IPS=127.0.0.1,::1 \
  tabularium doctor
```

The first lines name the data folder and the `.env` that were checked. With the
service running, the `port` check fails because the service holds the port: that one
is expected. Then, from outside:

```bash
curl https://tabularium.example.org/api/health
```

## 6. Backups and upgrades

```bash
sudo -u tabularium env HOME=/var/lib/tabularium TABULARIUM_ROOT=/var/lib/tabularium/data \
  tabularium backup --out /var/lib/tabularium/tabularium-backup-$(date +%F).tar.gz
```

The archive goes outside the data folder, somewhere the service user can write; copy it
off the server. It contains the vault key: keep it private. Before upgrading, back up, stop
the service, upgrade with the `uv tool upgrade` command of step 1, and start it again;
the database migrates on start, after an automatic copy. The full procedure and the
way back are in [Upgrading and rollback](upgrading.md).
