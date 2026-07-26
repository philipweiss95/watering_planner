class ConflictError(ValueError):
    """A valid request conflicts with already persisted domain state."""
