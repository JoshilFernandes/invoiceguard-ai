from pathlib import Path

import pytest

from invoiceguard.config import ROOT, Settings
from invoiceguard.service import InvoiceGuard

SAMPLES = ROOT / "data" / "samples"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(llm="offline", state_dir=tmp_path / "state")


@pytest.fixture
def guard(settings: Settings) -> InvoiceGuard:
    return InvoiceGuard(settings)


def sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()
