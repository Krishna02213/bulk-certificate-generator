import io
import zipfile

from app.api.routes import _safe_filename
from app.models import JobStatus


def test_create_job_generates_certificates_and_reports_progress(client, valid_payload):
    response = client.post("/api/v1/jobs", json=valid_payload)
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["total_count"] == 2
    assert accepted["valid_count"] == 2

    result = client.get(f"/api/v1/jobs/{accepted['job_id']}")
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == JobStatus.COMPLETED
    assert body["progress"] == {
        "total": 2,
        "pending": 0,
        "succeeded": 2,
        "failed": 0,
        "percent_complete": 100.0,
    }
    assert all(item["download_url"] for item in body["items"])


def test_top_level_input_validation(client, valid_payload):
    valid_payload["certificate"]["issue_date"] = "not-a-date"
    response = client.post("/api/v1/jobs", json=valid_payload)
    assert response.status_code == 422


def test_invalid_recipient_does_not_block_valid_recipient(client, valid_payload):
    valid_payload["recipients"][1]["email"] = "not-an-email"
    response = client.post("/api/v1/jobs", json=valid_payload)
    assert response.status_code == 202
    assert response.json()["invalid_count"] == 1

    body = client.get(f"/api/v1/jobs/{response.json()['job_id']}").json()
    assert body["status"] == JobStatus.COMPLETED_WITH_ERRORS
    assert body["progress"]["succeeded"] == 1
    assert body["progress"]["failed"] == 1
    assert body["items"][1]["status"] == "failed_validation"
    assert body["items"][1]["error_code"] == "INVALID_RECIPIENT"


def test_individual_generation_failure_is_isolated(client, app, valid_payload, monkeypatch):
    real_render = app.state.processor.renderer.render

    def selective_failure(item, job, destination):
        if item.recipient_email == "grace@example.com":
            raise RuntimeError("Synthetic renderer failure")
        return real_render(item, job, destination)

    monkeypatch.setattr(app.state.processor.renderer, "render", selective_failure)
    accepted = client.post("/api/v1/jobs", json=valid_payload).json()
    body = client.get(f"/api/v1/jobs/{accepted['job_id']}").json()

    assert body["status"] == "completed_with_errors"
    assert body["progress"]["succeeded"] == 1
    assert body["progress"]["failed"] == 1
    failed = next(item for item in body["items"] if item["status"] == "failed_generation")
    assert failed["error_code"] == "GENERATION_FAILED"
    assert "Synthetic renderer failure" in failed["error_message"]


def test_download_pdf_zip_and_verify(client, valid_payload):
    accepted = client.post("/api/v1/jobs", json=valid_payload).json()
    job = client.get(f"/api/v1/jobs/{accepted['job_id']}").json()
    first = job["items"][0]

    pdf = client.get(first["download_url"])
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF")

    bundle = client.get(job["download_url"])
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert len(archive.namelist()) == 2
        assert all(name.endswith(".pdf") for name in archive.namelist())

    verification = client.get(f"/api/v1/verify/{first['verification_code']}")
    assert verification.status_code == 200
    assert verification.json()["recipient_name"] == "Ada Lovelace"
    assert len(verification.json()["file_sha256"]) == 64


def test_idempotency_key_returns_same_job(client, valid_payload):
    headers = {"Idempotency-Key": "event-2026-upload-1"}
    first = client.post("/api/v1/jobs", json=valid_payload, headers=headers)
    second = client.post("/api/v1/jobs", json=valid_payload, headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.json()["valid_count"] == 2
    assert second.json()["invalid_count"] == 0


def test_download_filename_cannot_create_zip_paths():
    assert _safe_filename("../../Ada Lovelace") == "ada-lovelace"
    assert _safe_filename("Élodie") == "elodie"
