from __future__ import annotations
import queue
import time
import threading
from typing import Any
from ..ocr_runtime_types import DetectedGameWindow, OcrCaptureProfile, _CAPTURE_BACKEND_DXCAM, _DXCAM_GRAB_RETRY_ATTEMPTS, _DXCAM_GRAB_RETRY_DELAY_SECONDS, _STALE_CAPTURE_FRAME_THRESHOLD, _LOGGER
from ._helpers import _require_visible_capture_target, _target_screen_capture_rect, _crop_window_image

_DXCAM_CREATE_TIMEOUT_SECONDS = 5.0


def _create_dxcam_camera_with_timeout(dxcam_module: Any, *, timeout_seconds: float):
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def _create() -> None:
        try:
            result_queue.put(("ok", dxcam_module.create(output_color="RGB")))
        except Exception as exc:
            result_queue.put(("error", exc))

    thread = threading.Thread(
        target=_create,
        name="galgame-ocr-dxcam-create",
        daemon=True,
    )
    thread.start()
    try:
        status, payload = result_queue.get(timeout=max(0.01, float(timeout_seconds)))
    except queue.Empty as exc:
        thread.join(timeout=0.5)
        raise TimeoutError(
            f"dxcam_create_timed_out_after_{timeout_seconds:.1f}s"
        ) from exc
    if status == "error":
        raise payload
    return payload


class DxcamCaptureBackend:
    kind = _CAPTURE_BACKEND_DXCAM
    _MAX_CONSECUTIVE_FAILURES = 3
    _FAILURE_COOLDOWN_SECONDS = 30.0

    def __init__(self, *, logger=None) -> None:
        self._logger = logger
        self._camera = None
        self._camera_lock = threading.RLock()
        self._last_create_error = ""
        self._consecutive_failures = 0
        self._last_failure_time = 0.0

    def is_available(self) -> bool:
        try:
            import dxcam
            return bool(dxcam)
        except ImportError:
            return False

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}({target.pid}) {target.title}"

    def _camera_instance(self):
        with self._camera_lock:
            if self._camera is not None:
                return self._camera
            import dxcam

            last_exc = None
            for _attempt in range(3):
                try:
                    self._camera = _create_dxcam_camera_with_timeout(
                        dxcam,
                        timeout_seconds=_DXCAM_CREATE_TIMEOUT_SECONDS,
                    )
                except TimeoutError as exc:
                    self._record_create_failure(exc)
                    raise
                except Exception as exc:
                    last_exc = exc
                    self._last_create_error = str(exc)
                    time.sleep(0.5)
                    continue
                if self._camera is not None:
                    return self._camera
                time.sleep(0.5)
            if last_exc is not None:
                self._record_create_failure(last_exc)
                raise RuntimeError(f"dxcam_create_failed: {last_exc}") from last_exc
            exc = RuntimeError("dxcam_create_failed: returned None after retries")
            self._record_create_failure(exc)
            raise exc

    def _record_create_failure(self, exc: BaseException) -> None:
        self._last_create_error = str(exc)
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()

    def _reset_camera(self) -> None:
        with self._camera_lock:
            camera = self._camera
            self._camera = None
            stop = getattr(camera, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    _LOGGER.warning("ocr_reader camera stop() failed", exc_info=True)

    def capture_frame(self, target: DetectedGameWindow, profile: OcrCaptureProfile) -> Any:
        from PIL import Image

        _require_visible_capture_target(target, backend_kind=self.kind)
        rect = _target_screen_capture_rect(target)
        frame = None
        with self._camera_lock:
            now = time.monotonic()
            if (
                self._consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES
                and now - self._last_failure_time < self._FAILURE_COOLDOWN_SECONDS
            ):
                raise RuntimeError(
                    f"dxcam rate limited after {self._consecutive_failures} consecutive failures"
                )
            for attempt in range(_DXCAM_GRAB_RETRY_ATTEMPTS + 1):
                camera = self._camera_instance()
                frame = camera.grab(region=rect)
                if frame is not None:
                    self._consecutive_failures = 0
                    break
                self._reset_camera()
                self._consecutive_failures += 1
                self._last_failure_time = time.monotonic()
                if attempt < _DXCAM_GRAB_RETRY_ATTEMPTS:
                    time.sleep(_DXCAM_GRAB_RETRY_DELAY_SECONDS)
            if frame is None:
                raise RuntimeError(
                    f"dxcam_grab_returned_none_after_{_DXCAM_GRAB_RETRY_ATTEMPTS + 1}_attempts"
                )
        image = Image.fromarray(frame).convert("RGB")
        return _crop_window_image(
            image,
            window_rect=rect,
            profile=profile,
            backend_kind=self.kind,
            backend_detail="selected",
        )
