import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel


@dataclass(frozen=True)
class SavedUpload:
    path: Path
    filename: str
    original_filename: str | None
    content_type: str
    size_bytes: int


SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")


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

    def save_upload_file(
        self,
        upload: UploadFile,
        destination: Path,
        *,
        allowed_mime_types: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> SavedUpload:
        allowed = allowed_mime_types or self.ALLOWED_MIME_TYPES
        limit = max_bytes or self.MAX_FLOORPLAN_BYTES
        content_type = upload.content_type or ""
        if content_type not in allowed:
            inferred_content_type = self._infer_content_type_from_filename(upload.filename, allowed)
            if inferred_content_type:
                content_type = inferred_content_type
        if content_type not in allowed:
            allowed_types = ", ".join(sorted(allowed))
            raise HTTPException(status_code=415, detail=f"unsupported file MIME type; allowed types are {allowed_types}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_safe_destination(destination)
        total_bytes = 0
        try:
            with destination.open("wb") as output_file:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > limit:
                        raise HTTPException(status_code=413, detail=f"uploaded file exceeds the {limit} byte limit")
                    output_file.write(chunk)
        except HTTPException:
            self._cleanup_partial_files(destination)
            raise
        except OSError as exc:
            self._cleanup_partial_files(destination)
            raise HTTPException(status_code=500, detail="failed to save uploaded file") from exc
        finally:
            upload.file.seek(0)

        if total_bytes == 0:
            self._cleanup_partial_files(destination)
            raise HTTPException(status_code=400, detail="uploaded file is empty")

        return SavedUpload(
            path=destination,
            filename=destination.name,
            original_filename=self.sanitize_original_filename(upload.filename),
            content_type=content_type,
            size_bytes=total_bytes,
        )

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
            inferred_content_type = self._infer_content_type_from_filename(floorplan.filename, self.ALLOWED_MIME_TYPES)
            if inferred_content_type:
                content_type = inferred_content_type
        if content_type not in self.ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=415,
                detail="unsupported floorplan MIME type; allowed types are image/png, image/jpeg, and image/webp",
            )
        return self.ALLOWED_MIME_TYPES[content_type]

    @staticmethod
    def _infer_content_type_from_filename(filename: str | None, allowed_mime_types: dict[str, str]) -> str | None:
        if not filename:
            return None
        suffix = Path(filename).suffix.lower()
        for content_type, extension in allowed_mime_types.items():
            if suffix == extension:
                return content_type
        if suffix == ".jpeg" and "image/jpeg" in allowed_mime_types:
            return "image/jpeg"
        return None

    @staticmethod
    def sanitize_original_filename(filename: str | None) -> str | None:
        if not filename:
            return None
        safe_name = Path(str(filename).replace("\\", "/")).name.strip()
        safe_name = SAFE_FILENAME_PATTERN.sub("_", safe_name)
        safe_name = safe_name.strip(" .")
        return safe_name or None

    @staticmethod
    def _ensure_safe_destination(destination: Path) -> None:
        resolved_parent = destination.parent.resolve()
        resolved_destination_parent = destination.resolve().parent
        try:
            resolved_destination_parent.relative_to(resolved_parent)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="unsafe upload destination") from exc

    @staticmethod
    def _cleanup_partial_files(*paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
