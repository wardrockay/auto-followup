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

### Schedule Missing Followups
```http
POST /schedule-missing-followups
```

Schedules followups for all sent drafts that don't have any followups yet.

### Sync Followup IDs
```http
POST /sync-followup-ids
```

Synchronizes the `followup_ids` field for drafts that have followup tasks but are missing this field in the draft document.

### Migrate Pending to Scheduled
```http
POST /migrate-pending-to-scheduled
```

One-time migration: converts all followup tasks with status `pending` to `scheduled` for compatibility with Prospector UI.

### Update Followups Scheduled Flags
```http
POST /update-followups-scheduled-flags
```

Updates the `followups_scheduled` flag to `true` for drafts that have `followup_ids` but are missing this flag.

### Migrate to Old Schema
```http
POST /migrate-to-old-schema
```

Migrates followup documents from new schema (`days_after_sent`, `scheduled_date`) to old schema (`days_after_initial`, `scheduled_for`). This ensures compatibility with the Prospector UI which expects the old field names.

**Response:**
```json
{
    "success": true,
    "data": {
        "migrated_count": 42,
        "message": "Successfully migrated 42 followups to old schema (days_after_initial, scheduled_for)"
    }
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

## 📄 Firestore Schema

### Collection: `email_drafts`

Document structure for email drafts:

#### Required Fields
| Field | Type | Description |
|-------|------|-------------|
| `to` | string | Recipient email address |
| `subject` | string | Email subject |
| `body` | string | Email body content |
| `created_at` | timestamp | Creation timestamp |
| `status` | string | Draft status: `pending`, `approved`, `rejected`, `sent`, `bounced`, `replied` |
| `version_group_id` | string | Version group identifier |

#### Optional Fields
| Field | Type | Description |
|-------|------|-------------|
| `x_external_id` | string | External system ID |
| `odoo_id` | int/string | Odoo contact ID |
| `odoo_contact_id` | string | Alternative Odoo contact ID |
| `error_message` | string | Error message if applicable |

#### Followup-Related Fields
| Field | Type | Description |
|-------|------|-------------|
| `followup_number` | int | Followup sequence number (0 for initial email) |
| `is_followup` | boolean | Indicates if this is a followup email |
| `followup_ids` | array | List of followup task IDs created for this draft |
| `followups_scheduled` | boolean | Indicates if followup tasks have been scheduled |
| `no_followup` | boolean | If true, no followups will be scheduled |
| `reply_to_thread_id` | string | Gmail thread ID for replies |
| `reply_to_message_id` | string | Gmail message ID for replies |
| `original_subject` | string | Original subject for followup threads |

#### Contact Information
| Field | Type | Description |
|-------|------|-------------|
| `contact_name` | string | Contact full name |
| `contact_first_name` | string | Contact first name |
| `partner_name` | string | Company name |
| `company_name` | string | Alternative company name field |
| `function` | string | Contact job title |
| `website` | string | Company website |
| `description` | string | Company description |
| `recipient_email` | string | Alternative to `to` field |

#### Sender Information
| Field | Type | Description |
|-------|------|-------------|
| `sender_email` / `from_address` | string | Sender email address |
| `sender_name` / `from_name` | string | Sender display name |

#### Post-Send Fields
| Field | Type | Description |
|-------|------|-------------|
| `sent_at` | timestamp | Sending timestamp |
| `message_id` | string | Gmail message ID |
| `thread_id` | string | Gmail thread ID |

#### Additional Fields
| Field | Type | Description |
|-------|------|-------------|
| `notes` | string | Additional notes |

### Collection: `email_followups`

Document structure for followup tasks:

| Field | Type | Description |
|-------|------|-------------|
| `draft_id` | string | Reference to original draft document |
| `followup_number` | int | Followup sequence number (1-4) |
| `days_after_initial` | int | Days after original email was sent |
| `scheduled_for` | timestamp | When the followup should be processed |
| `status` | string | Task status: `scheduled`, `sent`, `failed`, `cancelled` |
| `created_at` | timestamp | Task creation timestamp |
| `processed_at` | timestamp | When the task was processed |
| `error_message` | string | Error message if processing failed |

**Note**: The service previously used `days_after_sent` and `scheduled_date` fields. Use the `/migrate-to-old-schema` endpoint to migrate existing documents to the current schema.

## 📋 License

MIT
