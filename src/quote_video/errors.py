"""Pipeline error codes, matching docs/reels-api-spec.md section 5
("오류 코드"). Each exception carries a `.code` the FastAPI layer (step 5)
will surface as `error.code` in the render-status response.
"""

from __future__ import annotations


class PipelineError(Exception):
    code = "PIPELINE_ERROR"


class CaptionTooLongError(PipelineError):
    code = "CAPTION_TOO_LONG"


class SceneMismatchError(PipelineError):
    code = "SCENE_MISMATCH"


class NoUsableMediaError(PipelineError):
    code = "NO_USABLE_MEDIA"
