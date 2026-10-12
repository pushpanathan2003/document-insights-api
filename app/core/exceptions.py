class DocumentInsightsError(Exception):
    pass


class DocumentNotFoundError(DocumentInsightsError):
    def __init__(self, document_id: str) -> None:
        super().__init__(f"Document '{document_id}' was not found")
        self.document_id = document_id


class InvalidDocumentIdError(DocumentInsightsError):
    def __init__(self, document_id: str) -> None:
        super().__init__(f"'{document_id}' is not a valid document id")
        self.document_id = document_id


class RateLimitExceededError(DocumentInsightsError):
    def __init__(self, user_id: str, limit: int) -> None:
        super().__init__(
            f"User '{user_id}' already has {limit} documents queued or processing. "
            "Wait for one of them to finish before submitting another."
        )
        self.user_id = user_id
        self.limit = limit


class ServiceUnavailableError(DocumentInsightsError):
    def __init__(self, dependency: str) -> None:
        super().__init__(f"{dependency} is currently unavailable. Please try again shortly.")
        self.dependency = dependency
