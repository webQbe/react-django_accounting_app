from django.db import models        # ORM base classes to define database tables as Python classes
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from django.conf import settings    # To access global project settings
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from django.db import models, transaction           # To wrap operations in a DB transaction
from django.utils import timezone                   # Timezone-aware datetime helper

# Choice Lists
AC_TYPES = [
    """ Used in Account model to classify general ledger accounts 
        First element = DB value (e.g., "asset"). Second element = human-readable label (e.g., "Asset")."""
    ("asset", "Asset"),
    ("liability", "Liability"),
    ("equity", "Equity"),
    ("income", "Income"),
    ("expense", "Expense"),
]

NORMAL_BALANCE = [
    """ Defines whether the account normally increases on the debit side (Assets, Expenses) or 
        credit side (Liabilities, Equity, Income)."""
    ("debit", "Debit"),
    ("credit", "Credit"),
]

JOURNAL_STATUS = [
    """ Used in JournalEntry.
        "draft" = still editable, "posted" = finalized, locked, "reversed" = reversal entry applied."""
    ("draft", "Draft"),
    ("posted", "Posted"),
    ("reversed", "Reversed"),
]

PAYMENT_METHODS = [
    """ Used in BankTransaction or Payment entities.
        Keeps payment method standardized across records. """
    ("cash", "Cash"),
    ("cheque", "Cheque"),
    ("bank_transfer", "Bank Transfer"),
    ("card", "Card"),
    ("other", "Other"),
]

