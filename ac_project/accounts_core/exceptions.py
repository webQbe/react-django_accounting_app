
class UnbalancedJournalError(Exception):
    """Raised when a JournalEntry fails double-entry balance check."""
    pass
