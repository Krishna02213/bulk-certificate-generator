import logging
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import select

from app.models import CertificateItem, GenerationJob, ItemStatus, JobStatus, utcnow
from app.services.renderer import CertificateRenderer

logger = logging.getLogger(__name__)


def new_verification_code() -> str:
    return secrets.token_hex(8).upper()


class JobProcessor:
    def __init__(
        self,
        session_factory,
        renderer: CertificateRenderer,
        storage_dir: Path,
        workers: int = 4,
    ):
        self.session_factory = session_factory
        self.renderer = renderer
        self.storage_dir = storage_dir
        self.workers = workers

    def process_job(self, job_id: str) -> None:
        try:
            work = self._start_job(job_id)
            if not work:
                self._finish_job(job_id)
                return

            with ThreadPoolExecutor(
                max_workers=self.workers, thread_name_prefix="certificate"
            ) as pool:
                futures = {
                    pool.submit(self._render_item, job_id, item_id): item_id for item_id in work
                }
                for future in as_completed(futures):
                    item_id = futures[future]
                    try:
                        file_path, digest = future.result()
                        self._mark_success(job_id, item_id, file_path, digest)
                    # This is the isolation boundary: one recipient cannot fail the batch.
                    except Exception as exc:
                        logger.exception("Certificate generation failed for item %s", item_id)
                        self._mark_failure(job_id, item_id, exc)
            self._finish_job(job_id)
        except Exception as exc:
            logger.exception("Job %s failed unexpectedly", job_id)
            self._mark_job_failed(job_id, exc)

    def _start_job(self, job_id: str) -> list[str]:
        with self.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status != JobStatus.QUEUED:
                return []
            job.status = JobStatus.PROCESSING
            job.started_at = utcnow()
            items = list(
                session.scalars(
                    select(CertificateItem).where(
                        CertificateItem.job_id == job_id,
                        CertificateItem.status == ItemStatus.PENDING,
                    )
                )
            )
            for item in items:
                item.status = ItemStatus.GENERATING
            session.commit()
            return [item.id for item in items]

    def _render_item(self, job_id: str, item_id: str) -> tuple[str, str]:
        with self.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            item = session.get(CertificateItem, item_id)
            if job is None or item is None:
                raise RuntimeError("Job or certificate item disappeared during processing")
            destination = self.storage_dir / job_id / f"{item_id}.pdf"
            digest = self.renderer.render(item, job, destination)
            return str(destination.resolve()), digest

    def _mark_success(self, job_id: str, item_id: str, file_path: str, digest: str) -> None:
        with self.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            item = session.get(CertificateItem, item_id)
            if job is None or item is None:
                return
            item.status = ItemStatus.SUCCEEDED
            item.file_path = file_path
            item.file_sha256 = digest
            item.completed_at = utcnow()
            job.success_count += 1
            job.pending_count -= 1
            session.commit()

    def _mark_failure(self, job_id: str, item_id: str, exc: Exception) -> None:
        with self.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            item = session.get(CertificateItem, item_id)
            if job is None or item is None:
                return
            item.status = ItemStatus.FAILED_GENERATION
            item.error_code = "GENERATION_FAILED"
            item.error_message = self._safe_error(exc)
            item.completed_at = utcnow()
            job.failure_count += 1
            job.pending_count -= 1
            session.commit()

    def _finish_job(self, job_id: str) -> None:
        with self.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == JobStatus.FAILED:
                return
            job.status = (
                JobStatus.COMPLETED_WITH_ERRORS
                if job.failure_count
                else JobStatus.COMPLETED
            )
            job.completed_at = utcnow()
            session.commit()

    def _mark_job_failed(self, job_id: str, exc: Exception) -> None:
        with self.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = self._safe_error(exc)
                job.completed_at = utcnow()
                session.commit()

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        return message[:500]
