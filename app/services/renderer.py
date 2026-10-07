import hashlib
import os
import tempfile
from pathlib import Path

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from app.models import CertificateItem, GenerationJob


class CertificateRenderer:
    """Renders one deterministic, predefined certificate design."""

    def __init__(self, public_base_url: str):
        self.public_base_url = public_base_url.rstrip("/")

    def render(self, item: CertificateItem, job: GenerationJob, destination: Path) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(suffix=".pdf", dir=destination.parent)
        os.close(fd)
        temporary_path = Path(temporary_name)
        try:
            self._draw(temporary_path, item, job)
            digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
            temporary_path.replace(destination)
            return digest
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    def _draw(self, path: Path, item: CertificateItem, job: GenerationJob) -> None:
        width, height = landscape(A4)
        pdf = canvas.Canvas(str(path), pagesize=(width, height), pageCompression=1)
        pdf.setTitle(f"{job.certificate_title} — {item.recipient_name}")
        pdf.setAuthor(job.issuer_name)

        navy = colors.HexColor("#132238")
        gold = colors.HexColor("#C69C4A")
        cream = colors.HexColor("#FAF7F0")
        pdf.setFillColor(cream)
        pdf.rect(0, 0, width, height, fill=1, stroke=0)
        pdf.setStrokeColor(navy)
        pdf.setLineWidth(3)
        pdf.rect(12 * mm, 12 * mm, width - 24 * mm, height - 24 * mm)
        pdf.setStrokeColor(gold)
        pdf.setLineWidth(1)
        pdf.rect(16 * mm, 16 * mm, width - 32 * mm, height - 32 * mm)

        pdf.setFillColor(gold)
        pdf.circle(width / 2, height - 38 * mm, 11 * mm, fill=0, stroke=1)
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawCentredString(width / 2, height - 40 * mm, "CF")

        pdf.setFillColor(navy)
        pdf.setFont("Helvetica-Bold", 28)
        pdf.drawCentredString(width / 2, height - 63 * mm, job.certificate_title.upper())
        pdf.setFont("Helvetica", 11)
        pdf.setFillColor(colors.HexColor("#536273"))
        pdf.drawCentredString(width / 2, height - 75 * mm, "THIS CERTIFIES THAT")

        self._fit_centered_text(
            pdf, item.recipient_name or "", width / 2, height - 99 * mm, 32, gold
        )
        pdf.setStrokeColor(gold)
        pdf.line(width * 0.25, height - 104 * mm, width * 0.75, height - 104 * mm)

        style = ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=13,
            leading=19,
            alignment=TA_CENTER,
            textColor=navy,
        )
        body = Paragraph(
            f"has successfully completed<br/><b>{self._escape(job.course_name)}</b>", style
        )
        body.wrapOn(pdf, width * 0.60, 35 * mm)
        body.drawOn(pdf, width * 0.20, height - 137 * mm)

        pdf.setFont("Helvetica", 10)
        pdf.setFillColor(colors.HexColor("#536273"))
        pdf.drawString(27 * mm, 29 * mm, f"Issued {job.issue_date.strftime('%d %B %Y')}")
        pdf.setFillColor(navy)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawCentredString(width / 2, 35 * mm, job.signatory_name or job.issuer_name)
        pdf.setStrokeColor(navy)
        pdf.line(width / 2 - 32 * mm, 32 * mm, width / 2 + 32 * mm, 32 * mm)
        pdf.setFont("Helvetica", 9)
        pdf.drawCentredString(width / 2, 27 * mm, job.signatory_title or "Authorized Signatory")

        verification_url = f"{self.public_base_url}/api/v1/verify/{item.verification_code}"
        widget = qr.QrCodeWidget(verification_url)
        bounds = widget.getBounds()
        size = 23 * mm
        drawing = Drawing(
            size,
            size,
            transform=[
                size / (bounds[2] - bounds[0]),
                0,
                0,
                size / (bounds[3] - bounds[1]),
                0,
                0,
            ],
        )
        drawing.add(widget)
        drawing.drawOn(pdf, width - 51 * mm, 22 * mm)
        pdf.setFont("Helvetica", 6.8)
        pdf.setFillColor(colors.HexColor("#536273"))
        pdf.drawCentredString(width - 39.5 * mm, 19 * mm, f"Verify: {item.verification_code}")

        pdf.showPage()
        pdf.save()

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def _fit_centered_text(pdf, text: str, center_x: float, y: float, max_size: int, color) -> None:
        size = max_size
        while size > 16 and stringWidth(text, "Helvetica-BoldOblique", size) > 170 * mm:
            size -= 1
        pdf.setFillColor(color)
        pdf.setFont("Helvetica-BoldOblique", size)
        pdf.drawCentredString(center_x, y, text)
