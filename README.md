# to.ALWISP — URL Shortener & QR Code Generator

A self-hosted URL shortener with QR code generation, click analytics, user authentication, and team workspaces. Built on Python/Flask + SQLite. Runs as a Docker container — designed for Unraid but works anywhere Docker runs.

---

## 📍 Project Roadmap

### ✅ Phase 1 — Core MVP (Complete)
- [x] URL shortening with random or custom codes
- [x] Click tracking with timestamp, referrer, user-agent
- [x] QR code generation per short link (backend-rendered PNG)
- [x] Custom QR generator with color and size controls
- [x] Link expiration support
- [x] Dashboard with stats (total links, total clicks, avg clicks/link)
- [x] Link management (view, copy, delete)
- [x] Dark-mode frontend with polished UI
- [x] REST API
- [x] SQLite database (zero config, single file, Docker volume)
- [x] Docker image with multi-stage build
- [x] Unraid-ready container config

### ✅ Phase 2 — Analytics & Management (Complete)
- [x] Click analytics chart (daily clicks over time, per-link)
- [x] Referrer breakdown
- [x] Device/browser breakdown from User-Agent parsing
- [x] Link editing (change destination, update expiry)
- [x] Search/filter links in dashboard
- [x] Link tags/categories

### ✅ Phase 3 — Auth & Multi-user (Complete)
- [x] User accounts with PBKDF2-hashed passwords
- [x] JWT access tokens (8h) + refresh tokens (30d) with rotation
- [x] Invite-only registration — admin generates invite links
- [x] First registered user automatically becomes admin
- [x] Per-user link ownership and dashboards
- [x] API key management (shown once, stored as hash)
- [x] Role-based access (admin / member)
- [x] Team workspaces — share links across members
- [x] Admin panel — manage users, invites, and all links

### 🔜 Phase 4 — Integrations & Power Features
- [ ] QR code with embedded logo/icon
- [ ] Custom domains per workspace
- [ ] Webhook on click events
- [ ] UTM parameter auto-append
- [ ] Browser extension integration

### 🔜 Phase 5 — Production Hardening
- [ ] Rate limiting per IP
- [ ] PostgreSQL/MySQL backend option
- [ ] Redis caching for hot links
- [ ] Bulk link import via CSV

---

## 🚀 First-Time Setup

On a fresh install, **the first registered account becomes admin** — no invite token needed.

1. Navigate to your instance URL
2. Click **Register** and create your admin account
3. From the **Admin** panel, generate invite links to add other users

> **Important:** Set a strong, unique `SECRET_KEY` before deploying. All JWT tokens are signed with this key — changing it after users have logged in will invalidate all active sessions.

---

## 🐳 Docker Deployment

### Option A — Build locally

```bash
# Generate a random SECRET_KEY first:
python3 -c "import secrets; print(secrets.token_hex(32))"

docker build -t sniplink:latest .

docker run -d \
  --name sniplink \
  --restart unless-stopped \
  -p 5000:5000 \
  -v sniplink-data:/app/data \
  -e BASE_URL=https://to.alwisp.com \
  -e SECRET_KEY=your-generated-key-here \
  sniplink:latest
```

### Option B — Docker Compose

```bash
# Edit docker-compose.yml first — set BASE_URL and SECRET_KEY
docker compose up -d
```

---

## 🖥 Unraid Setup (Step-by-Step)

### Step 1 — Get the image onto Unraid

**Option 1: Build directly on Unraid**
```bash
cd /mnt/user/appdata/sniplink-src
docker build -t sniplink:latest .
```

**Option 2: Push to Docker Hub (recommended)**
```bash
docker build -t yourdockerhubusername/sniplink:latest .
docker push yourdockerhubusername/sniplink:latest
```
Then use `yourdockerhubusername/sniplink:latest` as the repository in Unraid.

---

### Step 2 — Add container in Unraid UI

1. Go to **Docker** tab → **Add Container**
2. Fill in:

| Field | Value |
|---|---|
| **Name** | `sniplink` |
| **Repository** | `sniplink:latest` or your Docker Hub image |
| **Network Type** | `Bridge` |
| **Port Mapping** | Host `5000` → Container `5000` |
| **Path (Volume)** | Host `/mnt/user/appdata/sniplink` → Container `/app/data` |

3. Add **Environment Variables**:

| Key | Value | Notes |
|---|---|---|
| `BASE_URL` | `https://to.alwisp.com` | Your public domain |
| `SECRET_KEY` | *(long random string)* | **Required — never leave as default** |
| `DEBUG` | `false` | Keep false in production |

> Generate a strong key: `python3 -c "import secrets; print(secrets.token_hex(32))"`

4. Click **Apply**

---

### Step 3 — Reverse proxy via Nginx Proxy Manager

If you're using Nginx Proxy Manager on Unraid (the most common setup):

1. **Proxy Hosts** → **Add Proxy Host**
2. Set:
   - Domain: `to.alwisp.com`
   - Forward Hostname/IP: your Unraid LAN IP (e.g. `192.168.1.100`)
   - Forward Port: `5000`
3. On the **SSL** tab — request a free Let's Encrypt certificate
4. Ensure your DNS A record for `to.alwisp.com` points to your public IP

> **Note:** NPM strips `Authorization` headers by default. The app uses a custom `X-Auth-Token` header instead to bypass this — no special NPM configuration needed.

---

### Step 4 — Verify

```bash
docker inspect --format='{{.State.Health.Status}}' sniplink
# Should return: healthy
```

---

## 🗂 Project Structure

```
sniplink/
├── app.py              # Flask backend — all routes and logic
├── index.html          # Single-page frontend (served by Flask)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Multi-stage Docker build
├── docker-compose.yml  # For non-Unraid deployments
├── .dockerignore
└── README.md
```

---

## 🔌 API Reference

All authenticated endpoints require either:
- `X-Auth-Token: <jwt>` header, or
- `X-API-Key: <key>` header

### Auth

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register` | — | Register (invite required after first user) |
| POST | `/api/auth/login` | — | Login, returns access + refresh tokens |
| POST | `/api/auth/refresh` | — | Refresh access token |
| POST | `/api/auth/logout` | ✓ | Invalidate refresh token |
| GET | `/api/auth/me` | ✓ | Current user info |
| GET | `/api/auth/keys` | ✓ | List API keys |
| POST | `/api/auth/keys` | ✓ | Create API key |
| DELETE | `/api/auth/keys/:id` | ✓ | Revoke API key |

### Links

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/shorten` | optional | Shorten a URL |
| GET | `/api/links` | optional | List links (own links when authenticated) |
| GET | `/api/links/:code` | optional | Link detail |
| PATCH | `/api/links/:code` | ✓ | Edit link (owner or admin) |
| DELETE | `/api/links/:code` | ✓ | Delete link (owner or admin) |
| GET | `/api/links/:code/analytics` | optional | Click analytics |

### Workspaces

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/workspaces` | ✓ | List your workspaces |
| POST | `/api/workspaces` | ✓ | Create workspace |
| GET | `/api/workspaces/:id/members` | ✓ | List members |
| POST | `/api/workspaces/:id/members` | ✓ | Add member |

### Admin

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/admin/users` | admin | List all users |
| PATCH | `/api/admin/users/:id` | admin | Edit user (role, active status) |
| GET | `/api/admin/users/:id/links` | admin | User's links |
| POST | `/api/admin/invites` | admin | Generate invite link |
| GET | `/api/admin/invites` | admin | List all invites |

### Utilities

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/stats` | optional | Aggregate stats |
| GET | `/api/tags` | optional | All tags |
| GET | `/api/qr/:code` | — | QR PNG for a short link |
| GET | `/api/qr/custom` | — | QR for any URL |

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BASE_URL` | `https://to.alwisp.com` | Public URL of your instance |
| `PORT` | `5000` | Port Gunicorn listens on |
| `DEBUG` | `false` | Flask debug mode (keep false in production) |
| `SECRET_KEY` | *(none — required)* | JWT signing key — set a long random string |
| `DB_PATH` | `/app/data/sniplink.db` | SQLite file location (inside Docker volume) |
| `JWT_ACCESS_EXPIRY` | `28800` | Access token lifetime in seconds (default: 8h) |
| `JWT_REFRESH_EXPIRY` | `2592000` | Refresh token lifetime in seconds (default: 30d) |

---

## 🔄 Updating

Your data lives in the Docker volume and is preserved across updates.

```bash
docker build -t sniplink:latest .
docker stop sniplink && docker rm sniplink
docker run -d --name sniplink --restart unless-stopped \
  -p 5000:5000 -v sniplink-data:/app/data \
  -e BASE_URL=https://to.alwisp.com \
  -e SECRET_KEY=your-secret \
  sniplink:latest
```

On Unraid, click **Force Update** on the container in the Docker tab.

> **After updating:** Existing sessions remain valid as long as `SECRET_KEY` stays the same. If you change `SECRET_KEY`, all logged-in users will be asked to log in again — this is expected behavior.
