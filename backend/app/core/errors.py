class AppError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "app_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id: object) -> None:
        super().__init__(
            f"{resource} {resource_id!r} was not found",
            status_code=404,
            code="not_found",
        )


class ConflictError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409, code="conflict")


class PipelineError(Exception):
    """A safe, structured failure crossing Provider and Pipeline boundaries."""

    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        technical_detail: str | None = None,
        stage: str | None = None,
        retryable: bool = False,
        log_path: str | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.technical_detail = technical_detail
        self.stage = stage
        self.retryable = retryable
        self.log_path = log_path


class PipelineCancelled(PipelineError):
    def __init__(self, *, stage: str | None = None, log_path: str | None = None) -> None:
        super().__init__(
            "PROCESS_CANCELLED",
            "任务已由用户取消",
            stage=stage,
            retryable=True,
            log_path=log_path,
        )


class PipelineInterrupted(PipelineError):
    def __init__(self, *, stage: str | None = None) -> None:
        super().__init__(
            "backend_shutdown",
            "后端停止时任务被中断，可从任务详情重试",
            stage=stage,
            retryable=True,
        )
