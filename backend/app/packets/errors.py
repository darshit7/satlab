"""The one error every packet decoder raises, so callers can treat any malformed frame alike."""


class DecodeError(ValueError):
    """The bytes are not a well-formed frame of the expected kind (or exceed its limits)."""
