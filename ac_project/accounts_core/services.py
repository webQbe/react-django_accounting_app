from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.db import models

# Import models
from .models import (
    JournalEntry, JournalLine, BankTransaction, 
    BankTransactionInvoice, Invoice, FixedAsset,
    Period, Account
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


# ----------------------------
# Fixed Asset workflows
# ----------------------------
def depreciate_asset(asset_id, period_id, user=None):
    """
    Record depreciation for a fixed asset into the ledger.
    Workflow:
      1. Calculate depreciation for the period.
      2. Create a JournalEntry (if not already posted).
      3. Add JournalLines: 
         - Debit Depreciation Expense
         - Credit Accumulated Depreciation.
    """
    asset = FixedAsset.objects.select_for_update().get(pk=asset_id)
    period = Period.objects.get(pk=period_id)

    if not asset.useful_life_years or asset.useful_life_years <= 0:
        raise ValidationError("Asset must have a valid useful life")

    # Straight-line depreciation for simplicity
    depreciation_amount = asset.purchase_cost / asset.useful_life_years

    
    # Account for Depreciation Expense
    expense_acct = Account.objects.get(company=asset.company, code="6000") 
    # Account for Accumulated Depreciation
    accum_dep_acct = Account.objects.get(company=asset.company, code="1500")

    with transaction.atomic(): # depreciation entry + lines are either all committed or all rolled back

        # 1. Create JournalEntry header
        je = JournalEntry.objects.create(
            company=asset.company,
            period=period,
            date=timezone.now().date(),
            description=f"Depreciation for asset {asset.asset_code or asset.id}",
            status="draft",
            created_by=user,
            source_type="FixedAsset", # Traceability: link back to the FixedAsset
            source_id=asset.id,
        )

        # 2. Add JournalLines
        JournalLine.objects.create(
            company=asset.company,
            journal=je,
            account=expense_acct,
            description="Depreciation Expense",
            debit_amount=depreciation_amount,
            credit_amount=Decimal("0.00"),
            fixed_asset=asset,
        )

        JournalLine.objects.create(
            company=asset.company,
            journal=je,
            account=accum_dep_acct,
            description="Accumulated Depreciation",
            debit_amount=Decimal("0.00"),
            credit_amount=depreciation_amount,
            fixed_asset=asset,
        )

         # 3. Post (validates balance)
        je.post(user=user) 

    return je