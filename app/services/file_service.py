import json
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel


class FileService:
    MAX_FLOORPLAN_BYTES = 20 * 1024 * 1024
    ALLOWED_MIME_TYPES = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }

    def __init__(self, uploads_dir: Path, outputs_dir: Path, runs_dir: Path) -> None:
        self.uploads_dir = uploads_dir
        self.outputs_dir = outputs_dir
        self.runs_dir = runs_dir

    def create_run_id(self) -> str:
        return uuid4().hex

    def build_run_directory(self, run_id: str) -> Path:
        run_dir = self.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def save_floorplan(self, run_id: str, floorplan: UploadFile) -> Path:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        run_dir = self.build_run_directory(run_id)

        ext = self._resolve_extension(floorplan)
        uploads_path = self.uploads_dir / f"{run_id}_floorplan{ext}"
        run_floorplan_path = run_dir / f"floorplan{ext}"

        total_bytes = 0
        try:
            with uploads_path.open("wb") as uploads_file:
                while True:
                    chunk = floorplan.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > self.MAX_FLOORPLAN_BYTES:
                        raise HTTPException(status_code=413, detail="floorplan exceeds the 20MB limit")
                    uploads_file.write(chunk)
        except HTTPException:
            self._cleanup_partial_files(uploads_path, run_floorplan_path)
            raise
        except OSError as exc:
            self._cleanup_partial_files(uploads_path, run_floorplan_path)
            raise HTTPException(status_code=500, detail="failed to save uploaded floorplan") from exc
        finally:
            floorplan.file.seek(0)

        try:
            shutil.copyfile(uploads_path, run_floorplan_path)
        except OSError as exc:
            self._cleanup_partial_files(uploads_path, run_floorplan_path)
            raise HTTPException(status_code=500, detail="failed to copy floorplan into run folder") from exc

        return run_floorplan_path

    def save_analysis_json(self, run_id: str, analysis: BaseModel | dict) -> Path:
        return self.save_json_file(run_id, "analysis.json", analysis)

    def save_json_file(self, run_id: str, filename: str, payload: BaseModel | dict) -> Path:
        run_dir = self.build_run_directory(run_id)
        file_path = run_dir / filename

        if isinstance(payload, BaseModel):
            data = payload.model_dump(mode="json")
        else:
            data = payload

        try:
            with file_path.open("w", encoding="utf-8") as analysis_file:
                json.dump(data, analysis_file, ensure_ascii=False, indent=2)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to save {filename}") from exc

        return file_path

    def save_text_file(self, run_id: str, filename: str, content: str) -> Path:
        run_dir = self.build_run_directory(run_id)
        file_path = run_dir / filename

        try:
            with file_path.open("w", encoding="utf-8") as text_file:
                text_file.write(content)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"failed to save {filename}") from exc

        return file_path

    def copy_output_to_public(self, run_id: str, run_output_path: Path) -> Path:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        public_output_path = self.outputs_dir / f"{run_id}_output.png"

        try:
            shutil.copyfile(run_output_path, public_output_path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail="failed to copy output image into outputs directory") from exc

        return public_output_path

    def _resolve_extension(self, floorplan: UploadFile) -> str:
        content_type = floorplan.content_type or ""
        if content_type not in self.ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail="unsupported floorplan MIME type; allowed types are image/png, image/jpeg, and image/webp",
            )
        return self.ALLOWED_MIME_TYPES[content_type]

    @staticmethod
    def _cleanup_partial_files(*paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
