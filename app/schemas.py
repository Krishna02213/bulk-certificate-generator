from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import ItemStatus, JobStatus


class CertificateDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Certificate of Completion", min_length=3, max_length=120)
    course_name: str = Field(min_length=2, max_length=160)
    issuer_name: str = Field(min_length=2, max_length=160)
    issue_date: date
    signatory_name: str | None = Field(default=None, min_length=2, max_length=120)
    signatory_title: str | None = Field(default=None, min_length=2, max_length=120)

    @field_validator("title", "course_name", "issuer_name", "signatory_name", "signatory_title")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None


class RecipientInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    email: EmailStr

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not any(character.isalpha() for character in value):
            raise ValueError("name must contain at least one letter")
        return value


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    certificate: CertificateDetails
    recipients: list[dict[str, Any]] = Field(min_length=1)


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    total_count: int
    valid_count: int
    invalid_count: int
    status_url: str


class Progress(BaseModel):
    total: int
    pending: int
    succeeded: int
    failed: int
    percent_complete: float


class ItemResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: ItemStatus
    error_code: str | None
    error_message: str | None
    verification_code: str
    download_url: str | None = None


class JobResult(BaseModel):
    id: str
    status: JobStatus
    certificate_title: str
    course_name: str
    issuer_name: str
    issue_date: date
    progress: Progress
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    download_url: str | None
    items: list[ItemResult]


class VerificationResult(BaseModel):
    valid: bool = True
    verification_code: str
    recipient_name: str
    course_name: str
    issuer_name: str
    issue_date: date
    file_sha256: str

