
class UnbalancedJournalError(Exception):
    """Raised when a JournalEntry fails double-entry balance check."""
    pass

class AlreadyPostedDifferentPayload(Exception):
    """Raised when a JournalEntry already posted with different payload """
    pass
