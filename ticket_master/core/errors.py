"""Errors raised by the core. Messages are written so a person or an AI can fix the call."""


class TicketMasterError(Exception):
    """Base class for all Ticket Master errors."""


class NotFoundError(TicketMasterError):
    """A ticket, project, label or board does not exist."""


class ValidationError(TicketMasterError):
    """A value is missing or not allowed."""


class RuleError(TicketMasterError):
    """A workflow rule was broken, e.g. an invalid status move."""


class UndoConflictError(TicketMasterError):
    """A later change touched the same field, so undo needs force=True."""
