# Nginx Reverse Proxy for TRACE

This optional self-hosting configuration puts Nginx in front of the TRACE backend
and its same-origin frontend. It is not needed for Render, which exposes the web
service directly.

## Prerequisites

- Nginx installed on the self-hosted server.
- TRACE running on the same host at `127.0.0.1:8001`.
- A DNS name pointed at that server, if it will be accessed publicly.

## Setup (Linux)

1. Copy `deploy/nginx/qa-tracker.conf` to your Nginx site configuration directory.
2. Replace the example `server_name qa.example.com;` with your own DNS name.
   The former public IP was environment-specific and is intentionally not in this
   repository.
3. If the backend is not listening on `127.0.0.1:8001`, update `proxy_pass` to
   the backend address reachable from the Nginx host.
4. Enable the site using your distribution's normal Nginx configuration, then test
   and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Keep the backend bound to loopback (`HOST=127.0.0.1`, `PORT=8001`) so it is not
directly exposed to the Internet. Allow inbound ports 80/443 in the firewall; do
not open port 8001 publicly.

## HTTPS

Terminate TLS at Nginx. For a public DNS name, Certbot can configure a certificate:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d qa.example.com
```

The proxy preserves the request host and forwards client/protocol headers. The
30 MB Nginx body limit accommodates the application's upload request limit. Store
the SQLite database and `qa-platform/storage/` on persistent, backed-up storage;
for multi-instance hosting, this application requires shared durable file storage
in addition to PostgreSQL.
