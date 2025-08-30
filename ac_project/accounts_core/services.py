from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal

# Import models
from .models import (
    JournalEntry, JournalLine, BankTransaction, 
    BankTransactionInvoice, Invoice, FixedAsset
)

# ----------------------------
# Journal-related workflows
# ----------------------------
def post_journal_entry(journal_entry_id, user=None):
    """
    Wraps pure business logic with transaction management + orchestration
    """
    with transaction.atomic():
        # Lock the row to avoid race conditions
        je = JournalEntry.objects.select_for_update().get(pk=journal_entry_id)
        je.post(user=user)
    return je