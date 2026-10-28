# Self-Hosting QA Tracker

QA Tracker is now fully self-hostable. You can run it on any server (Linux VPS, NAS, home server) with Docker or directly with Python.

## Option 1: Docker (Recommended)

### Prerequisites
- Docker and Docker Compose installed
- Git (to clone/pull updates)

### Setup
```bash
# Clone the repo
git clone <your-repo-url> qa-tracker
cd qa-tracker

# Copy environment file
cp .env.example .env

# Generate encryption key and update .env
python3 -c "import secrets; print(secrets.token_hex(32))" >> /dev/null  # just to remind
# Edit .env - set QA_AI_ENCRYPTION_KEY and other values
nano .env

# Start
docker compose up -d --build
```

The app will be available at `http://YOUR_SERVER_IP:8001`. For production, put Nginx/Traefik in front with HTTPS.

### Data Persistence
All data is stored in `./qa-platform/` which is mounted as a volume. Back up this entire directory regularly.

## Option 2: Direct Python (No Docker)

### Prerequisites
- Python 3.10+ (3.11 recommended)
- Linux, macOS, or Windows

### Setup
```bash
# Clone repo
git clone <your-repo-url> qa-tracker
cd qa-tracker

# Setup backend
cd backend
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Setup env
cp ../.env.example ../qa-platform/.env
# Edit qa-platform/.env with your settings

# Run
python app.py
```

App runs on `http://127.0.0.1:8001` by default. Set `HOST=0.0.0.0` in `qa-platform/.env` for network access.

### Run as systemd service (Linux)
See the systemd unit example in README or create one pointing to your venv python and backend/app.py.

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| HOST / QA_HOST | 127.0.0.1 | Bind to 0.0.0.0 for external access |
| PORT / QA_PORT | 8001 | Port to listen on |
| DATABASE_URL | (unset) | Use PostgreSQL if set (e.g. postgresql://...) |
| QA_AI_ENCRYPTION_KEY | (unset) | Required if using encrypted AI provider keys |
| QA_ALLOW_PUBLIC_AUTH | true | Allow signup/login from non-localhost |
| FRONTEND_URL / CORS_ORIGINS | (unset) | For separate frontend hosting |
| FILE_STORAGE | local | local storage (persist qa-platform/storage/) |

## Security Recommendations
- Always use HTTPS in production (reverse proxy + Let's Encrypt)
- Keep `qa-platform/.env` and `qa-platform/` secure and backed up
- Use strong passwords for accounts
- Don't expose port directly if behind reverse proxy
- Keep the repo updated

## Backup
Back up the entire `qa-platform/` directory - it contains database, uploads, attachments, and config.
