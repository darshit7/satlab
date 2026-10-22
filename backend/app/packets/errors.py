class DecodeError(ValueError):
    """The bytes are not a well-formed frame of the expected kind (or exceed its limits)."""
