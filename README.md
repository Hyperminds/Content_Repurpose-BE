# TrendZZo — Backend API

AI-powered content operating system. Built with FastAPI, MongoDB, and OpenRouter AI.

## Tech Stack

- **Framework:** FastAPI (async Python)
- **Database:** MongoDB (Motor async driver)
- **AI:** OpenRouter API (GPT-4o-mini / free models)
- **Auth:** JWT (python-jose + bcrypt)
- **Realtime:** WebSockets
- **Deployment:** Docker + docker-compose

## Quick Start

```bash
# 1. Navigate to server directory
cd server

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Mac/Linux

# 3. Install dependencies
pip install -r app/requirements.txt

# 4. Configure environment
# Edit server/app/.env with your credentials

# 5. Start the server
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Server runs at `http://127.0.0.1:8000`

## Environment Variables

Create `server/app/.env`:

```env
# Environment mode: development | staging | production
APP_ENV=development

# AI (OpenRouter)
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Database
MONGODB_URL=mongodb://localhost:27017
DB_NAME=content_repurposer

# Auth
JWT_SECRET=your-secret-key
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=43200

# Email (SMTP)
SMTP_EMAIL=your-email@gmail.com
SMTP_PASSWORD=your-app-password

# LinkedIn OAuth
LINKEDIN_CLIENT_ID=your-client-id
LINKEDIN_CLIENT_SECRET=your-client-secret
LINKEDIN_REDIRECT_URI=http://localhost:8000/auth/linkedin/callback

# Frontend URL
FRONTEND_URL=http://localhost:5173
```

## Environment Modes

| Mode | Behavior |
|------|----------|
| `development` | Mock data, zero API credits consumed, DevToolbar active |
| `staging` | Partial real APIs, integration testing |
| `production` | Fully real APIs, rate limiting active, docs hidden |

## API Endpoints

### Core
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | App status |
| GET | `/health` | Health check (MongoDB, scheduler, AI, WebSockets) |
| GET | `/system/stats` | System stats (dev/staging only) |

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/signup` | Register new user |
| POST | `/auth/login` | Login, returns JWT |
| POST | `/auth/verify-otp` | Verify email OTP |
| POST | `/auth/forgot-password` | Request password reset |

### Content Generation
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/generate` | Generate content for all 7 platforms |

### Publishing
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/publishing/publish-now` | Instant publish to platform |
| POST | `/publishing/schedule` | Schedule future post |
| GET | `/publishing/history` | Get post history |
| GET | `/publishing/stats` | Publishing statistics |

### Social Presence Analyzer
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/social-presence/analyze` | Analyze profiles (username-only) |
| POST | `/social-presence/competitor-analysis` | Compare against competitors |
| POST | `/social-presence/growth-forecast` | Growth predictions |
| POST | `/social-presence/brand-positioning` | Brand analysis |
| POST | `/social-presence/content-strategy` | Monthly content plan |
| POST | `/social-presence/bio-optimization` | Optimize bio/headline |

### Trend Analysis
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/trends/fetch` | Fetch trends by category + platform |
| GET | `/trends/categories` | List supported categories |

### Campaigns
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/campaigns` | List campaigns |
| POST | `/campaigns` | Create campaign |
| POST | `/campaigns/{id}/generate-strategy` | AI strategy generation |

### Developer Sandbox (dev mode only)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/dev/status` | Sandbox status |
| POST | `/dev/simulate` | Toggle error simulation |
| POST | `/dev/reset` | Reset all simulations |
| POST | `/dev/preset/{name}` | Apply simulation preset |

### WebSocket
```
ws://localhost:8000/ws/{user_id}?channels=dashboard,notifications
```

## Architecture

```
server/
├── app/
│   ├── config.py              # Centralized settings
│   ├── database.py            # MongoDB connection + indexes
│   ├── main.py                # FastAPI app, middleware, routes
│   ├── middleware/
│   │   ├── error_handler.py   # Global exception handling
│   │   └── rate_limiter.py    # Request throttling
│   ├── mock_data/             # Development mode mock data
│   │   ├── analytics.py
│   │   ├── campaigns.py
│   │   ├── content_generation.py
│   │   ├── publishing.py
│   │   ├── social_presence.py
│   │   └── trends.py
│   ├── models/                # Pydantic models + MongoDB schemas
│   ├── routes/                # API route handlers
│   ├── services/              # Business logic layer
│   │   ├── background_tasks.py
│   │   ├── content_service.py
│   │   ├── dev_simulator.py
│   │   ├── feature_flags.py
│   │   ├── file_storage.py
│   │   ├── logger.py
│   │   ├── platform_adapters.py
│   │   ├── publishing_service.py
│   │   ├── social_presence_service.py
│   │   ├── token_tracker.py
│   │   └── trend_service.py
│   ├── utils/                 # JWT handler, helpers
│   └── websockets/
│       └── manager.py         # WebSocket connection manager
├── uploads/                   # Local file storage (dev)
├── Dockerfile
├── docker-compose.yml
└── .gitignore
```

## Docker Deployment

```bash
# Start MongoDB + API
docker-compose up -d

# Check health
curl http://localhost:8000/health
```

## Supported Platforms

- LinkedIn (OAuth auto-publish)
- Twitter/X (manual-assisted)
- Instagram (auto-publish)
- Reddit (OAuth auto-publish)
- Medium (OAuth auto-publish)
- Meta/Facebook (auto-publish)
- Quora (manual-assisted)

## License

Proprietary — Hyperminds.tech
