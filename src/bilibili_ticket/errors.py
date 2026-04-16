class AppError(Exception):
    """Base application error."""


class HumanInterventionRequired(AppError):
    """Raised when manual verification or takeover is required."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class OrderPreparationFailed(AppError):
    """Raised when prepare 接口返回非成功结果。"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
