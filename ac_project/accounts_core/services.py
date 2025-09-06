from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal
from django.db import models
from django.apps import apps
from typing import Any, List, Dict

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
        # user=None just means “this parameter is optional; 
        # if you don’t provide it, we’ll treat it as no user”
    return je


# ----------------------------
# Payment-related workflows
# ----------------------------
def apply_payment(bt_id: int, invoice_id: int, amount: float):
    """
    Apply part (or all) of a bank transaction to an invoice.
    Locks both rows during the operation.
    """
    with transaction.atomic(): # Everything inside either succeeds as one unit or rolls back if something fails

        # Lock the bank transaction and invoice rows until the transaction finishes
        bt = BankTransaction.objects.select_for_update().get(pk=bt_id)
        inv = Invoice.objects.select_for_update().get(pk=invoice_id)

        # Validate invoice outstanding
        if amount > inv.outstanding_amount:
            raise ValidationError("Payment exceeds invoice outstanding amount")

        # Validate bank transaction remaining capacity
        total_applied = (
            bt.banktransactioninvoice_set.aggregate(total=models.Sum("applied_amount"))["total"] or Decimal("0")
        )

        # Validation: prevent over-allocation
        if total_applied + amount > bt.amount:
            raise ValidationError("Applied amounts exceed bank transaction amount")

        # Create join row
            """ This represents X amount of this bank transaction settles this invoice """
        BankTransactionInvoice.objects.create( # Creates join record in M2M table
            bank_transaction=bt,
            invoice=inv,
            applied_amount=amount,
            company=bt.company,  # enforce tenancy
        )

        # Update invoice outstanding
        inv.outstanding_amount -= amount
        if inv.outstanding_amount <= 0:
            inv.status = "paid"
        inv.save(update_fields=["outstanding_amount", "status"])

        # Update bank transaction status
        total_applied += amount
        if total_applied >= bt.amount:
            bt.status = "fully_applied"
        else:
            bt.status = "partially_applied"
        bt.save(update_fields=["status"])

        return bt, inv
    
def apply_bank_tx(bank_tx_id: int, invoice_applications: List[Dict]):
    """
    Apply one bank transaction across multiple invoices atomically.
    Delegates each allocation to apply_payment().
    """
    with transaction.atomic():
        results = []
        for ap in invoice_applications: # Loop over each application (list of dicts)
            # Pull out invoice’s ID and amount to apply 
            invoice_id = ap["invoice_id"]
            amount = ap["amount"]
            # Call apply_payment() for each invoice
            # Get updated BankTransaction and Invoice
            bt, inv = apply_payment(bank_tx_id, invoice_id, amount)
            # Add updated objects into results list
            results.append((bt, inv))
        return results


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


# ------------------------------------
# Snapshot update workflows
# ------------------------------------
def update_snapshots_for_journal(journal: JournalEntry): 
    """Recalculate balances for accounts touched by this journal."""
    # Prevent circular import issue
    # Fetch models dynamically from Django app registry
    AccountBalanceSnapshot = apps.get_model("accounts_core", "AccountBalanceSnapshot")

    # Loop over each child JournalLine to update snapshot of corresponding account
    for line in journal.journalline_set.all(): 
        # journal.journalline_set.all() works because Django automatically gives you 
        # the reverse relation manager from (journal: JournalEntry) → JournalLine
        snapshot, _ = AccountBalanceSnapshot.objects.get_or_create(
            # Grab snapshot row for (company, account, date) or 
            # create one if missing
            company=journal.company,
            account=line.account,
            snapshot_date=journal.date,
        )
        # Update debit/credit aggregates
        # Add this line’s debit/credit to running balance for that day
        snapshot.debit_balance += line.debit_amount or 0 # don’t try to add None
        snapshot.credit_balance += line.credit_amount or 0
        snapshot.save() # Save updated snapshot



# ------------------------------------
# Invoice status update workflows
# ------------------------------------
"""Move invoice from draft → open (after validation)."""
def open_invoice(invoice: Invoice):
    if not invoice.lines.exists():
        # an invoice shouldn’t move out of draft without at least one line item
        raise ValidationError("Cannot open invoice with no lines")
    invoice.transition_to("open") 
    return invoice

"""Move invoice from open → paid (only when outstanding == 0)."""
def pay_invoice(invoice: Invoice):
    if invoice.outstanding != 0:
        # Otherwise, you’d be marking unpaid invoices as paid.
        raise ValidationError("Cannot mark invoice as paid until fully settled")
    invoice.transition_to("paid") 
    return invoice