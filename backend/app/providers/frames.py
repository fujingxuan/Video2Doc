from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageChops, ImageOps, ImageStat

from app.core.config import get_settings
from app.core.errors import PipelineError
from app.providers.process import CancelCheck, ExternalProcessRunner
from app.storage.workspace import WorkspacePaths, read_json, write_json


FrameStrategy = Literal["fixed_interval", "scene_change", "hybrid"]
ProgressCallback = Callable[[float | None, str], None]
_PTS_RE = re.compile(r"\bpts_time:(?P<value>-?\d+(?:\.\d+)?)")
_SCENE_RE = re.compile(r"lavfi\.scene_score=(?P<value>\d+(?:\.\d+)?)")


@dataclass(frozen=True, slots=True)
class FrameExtractionConfig:
    strategy: FrameStrategy = "hybrid"
    interval_seconds: float = 15.0
    scene_change_threshold: float = 0.35
    scene_min_gap_seconds: float = 1.5
    max_candidate_frames: int = 240
    normalized_max_width: int = 1600
    perceptual_hamming_threshold: int = 4
    visual_difference_threshold: float = 0.025
    transition_burst_seconds: float = 3.0

    def validate(self) -> None:
        if self.strategy not in {"fixed_interval", "scene_change", "hybrid"}:
            raise PipelineError("FRAME_CONFIG_INVALID", "不支持的抽帧策略")
        if self.interval_seconds <= 0 or self.scene_min_gap_seconds <= 0:
            raise PipelineError("FRAME_CONFIG_INVALID", "抽帧间隔必须大于 0")
        if not 0 <= self.scene_change_threshold <= 1:
            raise PipelineError("FRAME_CONFIG_INVALID", "画面变化阈值必须位于 0 到 1")
        if not 1 <= self.max_candidate_frames <= 10_000:
            raise PipelineError("FRAME_CONFIG_INVALID", "候选帧数量上限无效")
        if self.normalized_max_width < 320:
            raise PipelineError("FRAME_CONFIG_INVALID", "规范化帧宽度不能小于 320")
        if not 0 <= self.perceptual_hamming_threshold <= 64:
            raise PipelineError("FRAME_CONFIG_INVALID", "感知哈希阈值无效")
        if not 0 <= self.visual_difference_threshold <= 1:
            raise PipelineError("FRAME_CONFIG_INVALID", "视觉差异阈值无效")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FrameRecord:
    frame_id: str
    media_timestamp: float
    extraction_reason: str
    original_frame_path: str
    normalized_image_path: str
    width: int
    height: int
    file_size: int
    sha256: str
    perceptual_hash: str
    scene_change_score: float | None
    kept: bool = True
    drop_reason: str | None = None
    duplicate_of: str | None = None
    similarity: dict[str, float | int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FrameExtractionResult:
    records: tuple[FrameRecord, ...]
    manifest_path: str
    config_fingerprint: str
    reused: bool = False

    @property
    def kept(self) -> tuple[FrameRecord, ...]:
        return tuple(item for item in self.records if item.kept)

    @property
    def dropped_count(self) -> int:
        return len(self.records) - len(self.kept)


class VideoFrameExtractor:
    def __init__(self, executable: str, runner: ExternalProcessRunner | None = None) -> None:
        self.executable = executable
        self.runner = runner or ExternalProcessRunner()

    async def extract(
        self,
        media_path: Path,
        *,
        duration: float,
        paths: WorkspacePaths,
        config: FrameExtractionConfig,
        cancel_check: CancelCheck,
        progress: ProgressCallback,
    ) -> FrameExtractionResult:
        config.validate()
        if duration <= 0:
            raise PipelineError("FRAME_EXTRACTION_FAILED", "无法确定视频时长，不能安全抽帧")
        fingerprint = frame_config_fingerprint(media_path, config)
        manifest_path = paths.frames / "manifest.json"
        reusable = load_valid_frame_manifest(manifest_path, fingerprint)
        if reusable is not None:
            progress(1.0, f"复用 {len(reusable.records)} 个已验证候选帧")
            return reusable

        paths.frame_candidates.mkdir(parents=True, exist_ok=True)
        paths.frame_normalized.mkdir(parents=True, exist_ok=True)
        for partial in paths.frame_candidates.glob("*.part.jpg"):
            partial.unlink(missing_ok=True)
        output_pattern = paths.frame_candidates / "candidate_%06d.part.jpg"
        observations: list[tuple[float, float | None]] = []
        pending_timestamp: float | None = None

        def parse_metadata(line: str) -> None:
            nonlocal pending_timestamp
            pts_match = _PTS_RE.search(line)
            if pts_match:
                pending_timestamp = float(pts_match.group("value"))
                progress(
                    min(1.0, max(0.0, pending_timestamp / duration)),
                    f"正在抽取候选帧：{pending_timestamp:.2f}/{duration:.2f} 秒",
                )
                return
            score_match = _SCENE_RE.search(line)
            if score_match and pending_timestamp is not None:
                observations.append((pending_timestamp, float(score_match.group("value"))))
                pending_timestamp = None

        select_expression = _select_expression(config, duration)
        filter_graph = (
            f"select={select_expression},metadata=print:key=lavfi.scene_score"
        )
        try:
            await self.runner.run(
                [
                    self.executable,
                    "-hide_banner",
                    "-nostdin",
                    "-i",
                    media_path,
                    "-map",
                    "0:v:0",
                    "-vf",
                    filter_graph,
                    "-fps_mode",
                    "vfr",
                    "-q:v",
                    "2",
                    output_pattern,
                ],
                cwd=paths.root,
                log_dir=paths.logs,
                log_name="ffmpeg_frames",
                timeout_seconds=get_settings().media_process_timeout_seconds,
                cancel_check=cancel_check,
                stderr_callback=parse_metadata,
            )
        except PipelineError as exc:
            for partial in paths.frame_candidates.glob("*.part.jpg"):
                partial.unlink(missing_ok=True)
            if exc.code == "PROCESS_UNAVAILABLE":
                exc.code = "FFMPEG_UNAVAILABLE"
                exc.user_message = "找不到 FFmpeg，无法抽取视频帧"
            elif exc.code == "PROCESS_FAILED":
                exc.code = "FRAME_EXTRACTION_FAILED"
                exc.user_message = "FFmpeg 抽取视频帧失败"
            raise

        partials = sorted(paths.frame_candidates.glob("candidate_*.part.jpg"))
        if not partials:
            raise PipelineError("FRAME_EXTRACTION_FAILED", "FFmpeg 没有生成任何候选帧")
        if len(observations) != len(partials):
            for partial in partials:
                partial.unlink(missing_ok=True)
            raise PipelineError(
                "FRAME_TIMESTAMP_MISMATCH",
                "候选帧数量与媒体时间戳数量不一致",
                technical_detail=f"frames={len(partials)}, timestamps={len(observations)}",
            )

        records: list[FrameRecord] = []
        try:
            for index, (partial, observation) in enumerate(zip(partials, observations, strict=True)):
                timestamp, scene_score = observation
                if timestamp < -0.001 or timestamp > duration + 0.1:
                    raise PipelineError(
                        "FRAME_TIMESTAMP_INVALID",
                        "抽取帧的媒体时间戳超出视频时长",
                        technical_detail=f"timestamp={timestamp}, duration={duration}",
                    )
                frame_id = f"frame_{index:06d}"
                original_path = paths.frame_candidates / f"{frame_id}.jpg"
                partial.replace(original_path)
                normalized_path = paths.frame_normalized / f"{frame_id}.jpg"
                width, height = _normalize_image(
                    original_path, normalized_path, config.normalized_max_width
                )
                records.append(
                    FrameRecord(
                        frame_id=frame_id,
                        media_timestamp=max(0.0, timestamp),
                        extraction_reason=_extraction_reason(index, scene_score, config),
                        original_frame_path=str(original_path),
                        normalized_image_path=str(normalized_path),
                        width=width,
                        height=height,
                        file_size=normalized_path.stat().st_size,
                        sha256=_sha256_file(normalized_path),
                        perceptual_hash=_difference_hash(normalized_path),
                        scene_change_score=scene_score,
                    )
                )
        except Exception:
            for partial in paths.frame_candidates.glob("*.part.jpg"):
                partial.unlink(missing_ok=True)
            for partial in paths.frame_normalized.glob("*.part.jpg"):
                partial.unlink(missing_ok=True)
            raise

        _apply_conservative_dedup(records, config)
        payload = _manifest_payload(records, config, fingerprint, media_path, duration)
        write_json(manifest_path, payload)
        progress(
            1.0,
            f"候选帧 {len(records)}，保留 {sum(item.kept for item in records)}，"
            f"去重 {sum(not item.kept for item in records)}",
        )
        return FrameExtractionResult(tuple(records), str(manifest_path), fingerprint)


def frame_config_fingerprint(media_path: Path, config: FrameExtractionConfig) -> str:
    stat = media_path.stat()
    payload = {
        "schema_version": 1,
        "media_path": str(media_path.resolve()),
        "media_size": stat.st_size,
        "media_mtime_ns": stat.st_mtime_ns,
        "config": config.to_dict(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def load_valid_frame_manifest(
    manifest_path: Path, expected_fingerprint: str
) -> FrameExtractionResult | None:
    payload = read_json(manifest_path)
    if (
        not payload
        or payload.get("schema_version") != 1
        or payload.get("config_fingerprint") != expected_fingerprint
        or not isinstance(payload.get("frames"), list)
    ):
        return None
    records: list[FrameRecord] = []
    try:
        for raw in payload["frames"]:
            record = FrameRecord(**raw)
            original = Path(record.original_frame_path)
            normalized = Path(record.normalized_image_path)
            if (
                not original.is_file()
                or not normalized.is_file()
                or _sha256_file(normalized) != record.sha256
            ):
                return None
            records.append(record)
    except (TypeError, OSError):
        return None
    if not records:
        return None
    timestamps = [item.media_timestamp for item in records]
    if timestamps != sorted(timestamps):
        return None
    return FrameExtractionResult(
        tuple(records), str(manifest_path), expected_fingerprint, reused=True
    )


def perceptual_hash_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 64


def visual_difference(left: Path, right: Path) -> float:
    with Image.open(left) as left_image, Image.open(right) as right_image:
        left_feature = _visual_feature(left_image)
        right_feature = _visual_feature(right_image)
    difference = ImageChops.difference(left_feature, right_feature)
    mean = ImageStat.Stat(difference).mean[0]
    return max(0.0, min(1.0, mean / 255.0))


def _select_expression(config: FrameExtractionConfig, duration: float) -> str:
    cap_gap = duration / max(1, config.max_candidate_frames - 1)
    interval = f"{max(config.interval_seconds, cap_gap):.6f}"
    min_gap = f"{max(config.scene_min_gap_seconds, cap_gap):.6f}"
    threshold = f"{config.scene_change_threshold:.6f}"
    if config.strategy == "fixed_interval":
        return f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval})+0*scene"
    if config.strategy == "scene_change":
        return (
            f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{min_gap})"
            f"*gt(scene\\,{threshold})"
        )
    return (
        f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval})"
        f"+gte(t-prev_selected_t\\,{min_gap})*gt(scene\\,{threshold})"
    )


def _extraction_reason(
    index: int, scene_score: float | None, config: FrameExtractionConfig
) -> str:
    if index == 0:
        return "initial_frame"
    if config.strategy == "fixed_interval":
        return "fixed_interval"
    if scene_score is not None and scene_score >= config.scene_change_threshold:
        return "scene_change"
    return "fixed_interval" if config.strategy == "hybrid" else "scene_change"


def _normalize_image(source: Path, destination: Path, max_width: int) -> tuple[int, int]:
    partial = destination.with_name(f"{destination.stem}.part{destination.suffix}")
    with Image.open(source) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        if normalized.width > max_width:
            height = max(1, round(normalized.height * max_width / normalized.width))
            normalized = normalized.resize((max_width, height), Image.Resampling.LANCZOS)
        width, height = normalized.size
        normalized.save(partial, format="JPEG", quality=92, optimize=True)
    partial.replace(destination)
    return width, height


def _difference_hash(path: Path) -> str:
    with Image.open(path) as image:
        feature = _content_crop(image).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(feature.tobytes())
    bits = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            bits = (bits << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return f"{bits:016x}"


def _visual_feature(image: Image.Image) -> Image.Image:
    return _content_crop(image).convert("L").resize((64, 36), Image.Resampling.BILINEAR)


def _content_crop(image: Image.Image) -> Image.Image:
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    bottom = max(1, math.floor(rgb.height * 0.80))
    return rgb.crop((0, 0, rgb.width, bottom))


def _apply_conservative_dedup(
    records: list[FrameRecord], config: FrameExtractionConfig
) -> None:
    if len(records) >= 3:
        for index in range(1, len(records) - 1):
            previous, current, following = records[index - 1 : index + 2]
            if (
                current.extraction_reason == "scene_change"
                and current.media_timestamp - previous.media_timestamp
                <= config.transition_burst_seconds
                and following.media_timestamp - current.media_timestamp
                <= config.transition_burst_seconds
            ):
                outer_hamming = perceptual_hash_distance(
                    previous.perceptual_hash, following.perceptual_hash
                )
                outer_difference = visual_difference(
                    Path(previous.normalized_image_path),
                    Path(following.normalized_image_path),
                )
                if (
                    outer_hamming <= config.perceptual_hamming_threshold
                    and outer_difference <= config.visual_difference_threshold
                ):
                    current.kept = False
                    current.drop_reason = "transient_between_similar_frames"
                    current.duplicate_of = previous.frame_id
                    current.similarity = {
                        "outer_hamming_distance": outer_hamming,
                        "outer_visual_difference": round(outer_difference, 6),
                    }

    last_kept: FrameRecord | None = None
    for record in records:
        if not record.kept:
            continue
        if last_kept is None:
            last_kept = record
            continue
        hamming = perceptual_hash_distance(
            last_kept.perceptual_hash, record.perceptual_hash
        )
        difference = visual_difference(
            Path(last_kept.normalized_image_path),
            Path(record.normalized_image_path),
        )
        record.similarity = {
            "previous_kept_hamming_distance": hamming,
            "previous_kept_visual_difference": round(difference, 6),
        }
        if record.sha256 == last_kept.sha256:
            record.kept = False
            record.drop_reason = "exact_duplicate"
            record.duplicate_of = last_kept.frame_id
        elif (
            hamming <= config.perceptual_hamming_threshold
            and difference <= config.visual_difference_threshold
        ):
            record.kept = False
            record.drop_reason = "perceptual_duplicate"
            record.duplicate_of = last_kept.frame_id
        else:
            last_kept = record


def _manifest_payload(
    records: list[FrameRecord],
    config: FrameExtractionConfig,
    fingerprint: str,
    media_path: Path,
    duration: float,
) -> dict[str, Any]:
    kept = sum(item.kept for item in records)
    reasons: dict[str, int] = {}
    for item in records:
        if item.drop_reason:
            reasons[item.drop_reason] = reasons.get(item.drop_reason, 0) + 1
    return {
        "schema_version": 1,
        "config_fingerprint": fingerprint,
        "media_path": str(media_path),
        "media_duration": duration,
        "config": config.to_dict(),
        "effective_min_candidate_gap_seconds": duration
        / max(1, config.max_candidate_frames - 1),
        "candidate_count": len(records),
        "kept_count": kept,
        "dropped_count": len(records) - kept,
        "drop_reasons": reasons,
        "frames": [item.to_dict() for item in records],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
