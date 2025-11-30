# Auto-Followup Service

Automated email followup scheduling and processing service for prospecting campaigns.

## 🎯 Features

- **Followup Scheduling**: Automatically schedules followup emails based on French business days
- **Business Day Calculation**: Accounts for French holidays including Easter-based dates
- **Cancellation**: Cancel pending followups when a prospect responds
- **Processing**: Process due followups by triggering the mail-writer service
- **Retry**: Retry failed followup operations

## 🏗️ Architecture

This project follows **Clean Architecture** principles:

```
src/auto_followup/
├── __init__.py              # Package version
├── app.py                   # Flask application factory
├── config/                  # Configuration management
│   ├── __init__.py
│   └── settings.py          # Dataclass-based settings
├── core/                    # Business logic (no dependencies)
│   ├── __init__.py
│   ├── business_days.py     # French business day calculations
│   └── exceptions.py        # Domain exceptions
├── infrastructure/          # External dependencies
│   ├── __init__.py
│   ├── logging.py           # Structured JSON logging
│   ├── firestore/           # Firestore repositories
│   │   ├── __init__.py
│   │   ├── models.py        # Data models
│   │   └── repositories.py  # Repository pattern
│   └── http/                # HTTP clients
│       ├── __init__.py
│       ├── odoo_client.py   # Odoo CRM client
│       └── mail_writer_client.py  # Mail-writer client
├── services/                # Business logic orchestration
│   ├── __init__.py
│   ├── scheduler.py         # Followup scheduling
│   ├── cancellation.py      # Followup cancellation
│   ├── processor.py         # Followup processing
│   └── retry.py             # Retry failed operations
└── api/                     # HTTP layer
    ├── __init__.py
    └── routes.py            # Flask endpoints
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Google Cloud SDK (for Firestore)
- Docker (optional)

### Installation

```bash
# Clone the repository
cd auto-followup

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install development dependencies
make install-dev

# Or using pip directly
pip install -e ".[dev]"
```

### Configuration

Set the following environment variables:

```bash
# Required
export DRAFT_COLLECTION="email_drafts"
export FOLLOWUP_COLLECTION="email_followups"
export MAIL_WRITER_URL="https://your-mail-writer-service.run.app"
export ODOO_DB_URL="https://your-odoo-api.com"
export ODOO_SECRET="your-odoo-api-key"

# Optional
export ENVIRONMENT="development"  # or "production"
export PORT="8080"
```

### Running Locally

```bash
# Development server
make run

# With gunicorn (production-like)
make run-gunicorn
```

## 📡 API Endpoints

### Health Check
```http
GET /health
```

### Schedule Followups
```http
POST /schedule-followups
Content-Type: application/json

{
    "draft_id": "abc123"
}
```

### Cancel Followups
```http
POST /cancel-followups
Content-Type: application/json

{
    "draft_id": "abc123"
}
```

### Process Pending Followups
```http
POST /process-pending-followups
```

### Retry Failed Followups
```http
POST /retry-failed-followups
```

## 🧪 Testing

```bash
# Run tests
make test

# Run tests with coverage
make test-cov
```

## 🔧 Development

```bash
# Format code
make format

# Run linters
make lint

# Type checking
make type-check

# Run all checks
make pre-commit
```

## 🐳 Docker

```bash
# Build image
make docker-build

# Run container
make docker-run
```

## ☁️ Cloud Run Deployment

```bash
# Build and push to GCR
gcloud builds submit --tag gcr.io/YOUR_PROJECT/auto-followup

# Deploy to Cloud Run
gcloud run deploy auto-followup \
    --image gcr.io/YOUR_PROJECT/auto-followup \
    --platform managed \
    --region europe-west1 \
    --set-env-vars "DRAFT_COLLECTION=email_drafts,FOLLOWUP_COLLECTION=email_followups,..." \
    --allow-unauthenticated
```

## 📅 Followup Schedule

The default followup schedule is:

| Followup # | Days After Sent |
|------------|-----------------|
| 1          | 3 business days |
| 2          | 7 business days |
| 3          | 10 business days|
| 4          | 180 business days|

This can be configured via the `FOLLOWUP_SCHEDULE` environment variable.

## 📋 License

MIT
