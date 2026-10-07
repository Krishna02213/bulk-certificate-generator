import io
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import CertificateItem, GenerationJob, ItemStatus, JobStatus, utcnow
from app.schemas import (
    ItemResult,
    JobAccepted,
    JobCreate,
    JobResult,
    Progress,
    RecipientInput,
    VerificationResult,
)
from app.services.processor import new_verification_code

router = APIRouter(prefix="/api/v1")


def get_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def _safe_filename(name: str | None) -> str:
    """Return a path-free ASCII filename stem suitable for HTTP and ZIP output."""
    normalized = unicodedata.normalize("NFKD", name or "certificate")
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    stem = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    return stem[:80] or "certificate"


def _validation_message(error: ValidationError) -> str:
    messages = []
    for detail in error.errors(include_url=False):
        location = ".".join(str(part) for part in detail["loc"])
        messages.append(f"{location}: {detail['msg']}" if location else detail["msg"])
    return "; ".join(messages)[:500]


@router.post("/jobs", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    payload: JobCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
):
    settings = request.app.state.settings
    if len(payload.recipients) > settings.max_recipients:
        raise HTTPException(413, f"A job may contain at most {settings.max_recipients} recipients")

    if idempotency_key:
        existing = session.scalar(
            select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
        )
        if existing:
            return _accepted(existing, request)

    valid: list[tuple[int, RecipientInput]] = []
    invalid: list[tuple[int, dict, str]] = []
    for position, raw in enumerate(payload.recipients):
        try:
            valid.append((position, RecipientInput.model_validate(raw)))
        except ValidationError as exc:
            invalid.append((position, raw, _validation_message(exc)))

    details = payload.certificate
    job = GenerationJob(
        certificate_title=details.title,
        course_name=details.course_name,
        issuer_name=details.issuer_name,
        issue_date=details.issue_date,
        signatory_name=details.signatory_name,
        signatory_title=details.signatory_title,
        total_count=len(payload.recipients),
        pending_count=len(valid),
        failure_count=len(invalid),
        validation_failure_count=len(invalid),
        idempotency_key=idempotency_key,
    )
    session.add(job)
    session.flush()
    for position, recipient in valid:
        session.add(
            CertificateItem(
                job_id=job.id,
                position=position,
                recipient_name=recipient.name,
                recipient_email=str(recipient.email),
                status=ItemStatus.PENDING,
                verification_code=new_verification_code(),
            )
        )
    for position, raw, error in invalid:
        session.add(
            CertificateItem(
                job_id=job.id,
                position=position,
                recipient_name=str(raw.get("name", ""))[:160] or None,
                recipient_email=str(raw.get("email", ""))[:320] or None,
                status=ItemStatus.FAILED_VALIDATION,
                error_code="INVALID_RECIPIENT",
                error_message=error,
                verification_code=new_verification_code(),
                completed_at=utcnow(),
            )
        )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if idempotency_key:
            existing = session.scalar(
                select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
            )
            if existing:
                return _accepted(existing, request)
        raise

    background_tasks.add_task(request.app.state.processor.process_job, job.id)
    return _accepted(job, request)


def _accepted(job: GenerationJob, request: Request) -> JobAccepted:
    return JobAccepted(
        job_id=job.id,
        status=job.status,
        total_count=job.total_count,
        valid_count=job.total_count - job.validation_failure_count,
        invalid_count=job.validation_failure_count,
        status_url=str(request.url_for("get_job", job_id=job.id)),
    )


@router.get("/jobs/{job_id}", response_model=JobResult, name="get_job")
def get_job(job_id: str, request: Request, session: SessionDep):
    job = session.scalar(
        select(GenerationJob)
        .options(selectinload(GenerationJob.items))
        .where(GenerationJob.id == job_id)
    )
    if job is None:
        raise HTTPException(404, "Generation job not found")
    return _job_result(job, request)


def _job_result(job: GenerationJob, request: Request) -> JobResult:
    completed = job.success_count + job.failure_count
    percent = round((completed / job.total_count) * 100, 2) if job.total_count else 100.0
    items = []
    for item in job.items:
        result = ItemResult.model_validate(item)
        if item.status == ItemStatus.SUCCEEDED:
            result.download_url = str(
                request.url_for("download_certificate", job_id=job.id, item_id=item.id)
            )
        items.append(result)
    bundle_url = None
    if job.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS} and job.success_count:
        bundle_url = str(request.url_for("download_job", job_id=job.id))
    return JobResult(
        id=job.id,
        status=job.status,
        certificate_title=job.certificate_title,
        course_name=job.course_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        progress=Progress(
            total=job.total_count,
            pending=job.pending_count,
            succeeded=job.success_count,
            failed=job.failure_count,
            percent_complete=percent,
        ),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        download_url=bundle_url,
        items=items,
    )


@router.get("/jobs/{job_id}/certificates/{item_id}", name="download_certificate")
def download_certificate(job_id: str, item_id: str, session: SessionDep):
    item = session.scalar(
        select(CertificateItem).where(
            CertificateItem.id == item_id, CertificateItem.job_id == job_id
        )
    )
    if item is None:
        raise HTTPException(404, "Certificate not found")
    if item.status != ItemStatus.SUCCEEDED or not item.file_path:
        raise HTTPException(409, "Certificate is not available")
    path = Path(item.file_path)
    if not path.is_file():
        raise HTTPException(410, "Certificate file is no longer available")
    safe_name = _safe_filename(item.recipient_name)
    return FileResponse(path, media_type="application/pdf", filename=f"{safe_name}.pdf")


@router.get("/jobs/{job_id}/download", name="download_job")
def download_job(job_id: str, session: SessionDep):
    job = session.scalar(
        select(GenerationJob)
        .options(selectinload(GenerationJob.items))
        .where(GenerationJob.id == job_id)
    )
    if job is None:
        raise HTTPException(404, "Generation job not found")
    if job.status not in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS}:
        raise HTTPException(409, "Job is not complete")
    successful = [
        item
        for item in job.items
        if item.status == ItemStatus.SUCCEEDED and item.file_path
    ]
    if not successful:
        raise HTTPException(404, "This job has no generated certificates")

    archive = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for item in successful:
            path = Path(item.file_path or "")
            if not path.is_file():
                continue
            base = _safe_filename(item.recipient_name)
            filename = f"{base}.pdf"
            if filename in used_names:
                filename = f"{base}-{item.position + 1}.pdf"
            used_names.add(filename)
            bundle.write(path, filename)
    archive.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="certificates-{job_id}.zip"'}
    return StreamingResponse(archive, media_type="application/zip", headers=headers)


@router.get("/verify/{verification_code}", response_model=VerificationResult)
def verify_certificate(verification_code: str, session: SessionDep):
    item = session.scalar(
        select(CertificateItem)
        .options(selectinload(CertificateItem.job))
        .where(
            CertificateItem.verification_code == verification_code.upper(),
            CertificateItem.status == ItemStatus.SUCCEEDED,
        )
    )
    if item is None or item.job is None or not item.file_sha256:
        raise HTTPException(404, "Valid certificate not found")
    return VerificationResult(
        verification_code=item.verification_code,
        recipient_name=item.recipient_name or "",
        course_name=item.job.course_name,
        issuer_name=item.job.issuer_name,
        issue_date=item.job.issue_date,
        file_sha256=item.file_sha256,
    )


@router.get("/jobs", response_model=list[JobResult])
def list_jobs(
    request: Request,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    jobs = session.scalars(
        select(GenerationJob)
        .options(selectinload(GenerationJob.items))
        .order_by(GenerationJob.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [_job_result(job, request) for job in jobs]
