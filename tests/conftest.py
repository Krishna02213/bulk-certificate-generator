from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            storage_dir=tmp_path / "certificates",
            public_base_url="http://testserver",
            worker_threads=2,
        )
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def valid_payload():
    return {
        "certificate": {
            "title": "Certificate of Excellence",
            "course_name": "Reliable API Engineering",
            "issuer_name": "Northstar Learning",
            "issue_date": "2026-10-07",
            "signatory_name": "Avery Sharma",
            "signatory_title": "Program Director",
        },
        "recipients": [
            {"name": "Ada Lovelace", "email": "ada@example.com"},
            {"name": "Grace Hopper", "email": "grace@example.com"},
        ],
    }

