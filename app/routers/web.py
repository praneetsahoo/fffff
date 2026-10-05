"""Serves the single-page web app and downloadable sample datasets."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.errors import NotFoundError

router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_data"


def _samples() -> dict[str, Path]:
    """Whitelist = files actually present in sample_data/ (and its edge_cases/ folder)."""
    files: dict[str, Path] = {}
    for folder in (SAMPLE_DIR, SAMPLE_DIR / "edge_cases"):
        if folder.is_dir():
            for f in sorted(folder.iterdir()):
                if f.is_file():
                    files[f.name] = f
    return files


@router.get("/api/samples", tags=["samples"], summary="Sample datasets for trying the platform")
def list_samples() -> list[dict]:
    return [{"name": name, "size_bytes": path.stat().st_size,
             "kind": "edge_case" if path.parent.name == "edge_cases" else "dataset"}
            for name, path in _samples().items()]


@router.get("/api/samples/{name}", tags=["samples"], summary="Download a sample file",
            response_class=FileResponse)
def download_sample(name: str) -> FileResponse:
    path = _samples().get(name)  # lookup by exact name: no path traversal possible
    if path is None:
        raise NotFoundError("Sample file not found.")
    return FileResponse(path, filename=name, media_type="text/csv")


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
