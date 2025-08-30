from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.db import models

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


# ----------------------------
# Payment-related workflows
# ----------------------------
def apply_payment(bt_id, invoice_id, amount):
    """
    Apply a bank transaction to an invoice.
    Ensures allocations never exceed the available transaction amount.
    """
    with transaction.atomic(): # Everything inside either succeeds as one unit or rolls back if something fails

        # 1. Lock the bank transaction row until the transaction finishes
        bt = BankTransaction.objects.select_for_update().get(pk=bt_id)

        # 2. Sum all amounts already applied from this bank transaction
        total_applied = (
            BankTransactionInvoice.objects
            .filter(bank_transaction=bt)
            .aggregate(total=models.Sum("applied_amount"))
            ["total"] or Decimal("0.00") # If none exist, defaults to 0.00
        )

        # 3. Validation: prevent over-allocation
        if total_applied + amount > bt.amount:
            raise ValidationError("Applied amounts exceed bank transaction amount")

        # 4. Record the allocation (link BankTransaction → Invoice)
            """ This represents X amount of this bank transaction settles this invoice """
        BankTransactionInvoice.objects.create( # Creates join record in M2M table
            company = bt.company,
            bank_transaction = bt,
            invoice_id = invoice_id,
            applied_amount = amount
        )

        # 5. Reduce the outstanding balance on the invoice
        inv = Invoice.objects.get(pk=invoice_id)
        inv.outstanding_amount = max( # Ensure it never goes negative
                                        Decimal("0.00"),
                                        inv.outstanding_amount - amount
                                    )
        inv.save()

    return True