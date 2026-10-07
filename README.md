# CertiFlow — Bulk Certificate Generator

CertiFlow is an interview-ready FastAPI backend that accepts one bulk request, validates every recipient independently, generates polished PDF certificates in parallel, tracks durable progress in a relational database, and provides individual PDF and ZIP downloads.

Each certificate also carries a QR code and short verification code. The public verification endpoint returns the certificate metadata and SHA-256 fingerprint, making a generated document independently verifiable.

## Highlights

- Bulk jobs of up to 5,000 recipients (configurable)
- Per-recipient validation and generation failure isolation
- Durable job/item state in SQLite through SQLAlchemy (PostgreSQL-ready)
- Bounded parallel PDF rendering with atomic file writes
- Live counters and explicit terminal states
- Individual PDF and whole-job ZIP downloads
- QR-based verification and SHA-256 file fingerprints
- `Idempotency-Key` support for safely retrying submissions
- OpenAPI documentation, typed schemas, and meaningful HTTP errors
- Test coverage for every requirement in the assignment

## Architecture

```text
HTTP request
    │
    ├── validate job metadata (invalid → 422)
    └── validate each recipient
            │
            ├── invalid → persisted as failed_validation
            └── valid   → persisted as pending
                              │
                         background job
                              │
                     bounded thread pool
                       ┌──────┼──────┐
                     PDF    PDF    PDF        failures remain isolated
                       └──────┼──────┘
                              │
                    status + counters in DB
                              │
                    PDF / ZIP / verification
```

The API uses an in-process FastAPI background task because this assignment should remain easy to run with one command and no infrastructure beyond a relational database. Rendering within a job uses a bounded thread pool because PDF rendering is independent and moderately CPU/I/O heavy. All database writes happen through short-lived sessions, and each file is first written to a temporary path then atomically moved into place.

This provides good behavior for a single application instance. For horizontal scaling or guaranteed recovery after a process crash, the `JobProcessor` service boundary can be moved to Celery/RQ/Dramatiq with Redis or RabbitMQ without changing the HTTP contracts or database model. On service startup, a production version would also requeue interrupted `processing` jobs.

## Requirements

- Python 3.11+
- No external database is needed for local use; SQLite is the default relational database.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
cp .env.example .env
```

## Run

```bash
uvicorn app.main:app --reload
```

Then open:

- Interactive API documentation: <http://localhost:8000/docs>
- Alternative API reference: <http://localhost:8000/redoc>
- Health check: <http://localhost:8000/health>

SQLite data and generated PDFs are written beneath `./data/` by default.

## Submit a bulk job

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: cohort-october-2026-v1' \
  -d '{
    "certificate": {
      "title": "Certificate of Excellence",
      "course_name": "Reliable API Engineering",
      "issuer_name": "Northstar Learning",
      "issue_date": "2026-10-07",
      "signatory_name": "Avery Sharma",
      "signatory_title": "Program Director"
    },
    "recipients": [
      {"name": "Ada Lovelace", "email": "ada@example.com"},
      {"name": "Grace Hopper", "email": "grace@example.com"},
      {"name": "Bad Address", "email": "not-an-email"}
    ]
  }'
```

The response is `202 Accepted`:

```json
{
  "job_id": "f24db8b4-0a64-4f8d-a838-dde2554c4ffc",
  "status": "queued",
  "total_count": 3,
  "valid_count": 2,
  "invalid_count": 1,
  "status_url": "http://localhost:8000/api/v1/jobs/f24db8b4-0a64-4f8d-a838-dde2554c4ffc"
}
```

The idempotency header is optional. Reusing the same value returns the original job instead of creating duplicate certificates.

## Check progress and results

```bash
curl http://localhost:8000/api/v1/jobs/JOB_ID
```

Possible job states are `queued`, `processing`, `completed`, `completed_with_errors`, and `failed`. Each recipient includes its own state, error code/message where applicable, verification code, and download URL after success.

Example progress fragment:

```json
{
  "status": "completed_with_errors",
  "progress": {
    "total": 3,
    "pending": 0,
    "succeeded": 2,
    "failed": 1,
    "percent_complete": 100.0
  }
}
```

List recent jobs with pagination:

```bash
curl 'http://localhost:8000/api/v1/jobs?limit=20&offset=0'
```

## Retrieve certificates

The job response contains canonical URLs. They follow these forms:

```bash
# One PDF
curl -OJ http://localhost:8000/api/v1/jobs/JOB_ID/certificates/ITEM_ID

# ZIP containing all successful PDFs (failed items are omitted)
curl -OJ http://localhost:8000/api/v1/jobs/JOB_ID/download

# Verify the certificate represented by its QR code
curl http://localhost:8000/api/v1/verify/VERIFICATION_CODE
```

Download attempts return `409 Conflict` while generation is in progress. A missing database record returns `404`; a database record whose file was removed returns `410 Gone`.

## Validation behavior

There are two intentionally different validation levels:

1. Invalid request structure or certificate metadata rejects the whole request with FastAPI's `422` response.
2. Invalid recipient fields are saved as `failed_validation` items. Other recipients continue. This matches bulk-import workflows, gives the caller an auditable outcome for every submitted row, and avoids forcing a large corrected resubmission.

Recipient names are normalized, must contain a letter, and are limited to 160 characters. Email addresses are syntax-validated. Unknown fields are rejected so client typos do not disappear silently.

## Run tests and quality checks

```bash
pytest --cov=app --cov-report=term-missing
ruff check .
```

The tests cover:

- job creation and progress counters
- request-level validation
- per-recipient validation
- real PDF generation
- one renderer failure without batch failure
- PDF and ZIP retrieval
- certificate verification
- idempotent submissions

## API summary

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/jobs` | Submit a bulk generation job |
| `GET` | `/api/v1/jobs/{job_id}` | Read progress and per-item results |
| `GET` | `/api/v1/jobs` | List recent jobs |
| `GET` | `/api/v1/jobs/{job_id}/certificates/{item_id}` | Download one successful PDF |
| `GET` | `/api/v1/jobs/{job_id}/download` | Download all successful PDFs as ZIP |
| `GET` | `/api/v1/verify/{code}` | Verify generated certificate metadata |
| `GET` | `/health` | Liveness check |

## Configuration

All settings use the `CERTIFLOW_` prefix:

| Variable | Default | Meaning |
|---|---|---|
| `CERTIFLOW_DATABASE_URL` | `sqlite:///./data/certiflow.db` | SQLAlchemy database URL |
| `CERTIFLOW_STORAGE_DIR` | `./data/certificates` | Generated PDF root |
| `CERTIFLOW_MAX_RECIPIENTS` | `5000` | Maximum rows in one request |
| `CERTIFLOW_WORKER_THREADS` | `4` | Concurrent renders within a job |
| `CERTIFLOW_PUBLIC_BASE_URL` | `http://localhost:8000` | Base URL embedded in QR codes |

For PostgreSQL, install a driver such as `psycopg[binary]` and set `CERTIFLOW_DATABASE_URL=postgresql+psycopg://...`. For schema evolution in a longer-lived product, add Alembic migrations rather than relying on startup `create_all`.

## Design trade-offs and next steps

- **Storage:** local disk is intentionally simple. Object storage (S3/GCS) plus short-lived signed URLs is the natural multi-instance upgrade.
- **Delivery:** the API exposes downloads; email delivery is kept outside the generation transaction and could consume a `certificate.succeeded` event.
- **Security:** real deployments should put authentication/authorization in front of job and download routes. Verification is intentionally public but reveals only certificate metadata.
- **Observability:** structured logs, metrics for render duration/failures, and a request correlation ID would be the next operational additions.
- **Retention:** add a scheduled retention policy for PDFs and job metadata based on the organization's privacy requirements.

