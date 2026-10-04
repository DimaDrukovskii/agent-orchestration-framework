class CallProcessingError(Exception):
    """Base exception for call processing errors"""
    pass


class AudioTranscriptionError(CallProcessingError):
    """Error during audio transcription"""
    pass


class LLMError(CallProcessingError):
    """Error in LLM service"""
    pass


class StorageError(CallProcessingError):
    """Error in storage operations"""
    pass


class CallNotFoundError(CallProcessingError):
    """Call record not found"""
    pass
