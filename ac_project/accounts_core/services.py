from decimal import Decimal
from typing import Dict, List, Iterable
from django.db.models.functions import Coalesce
from django.db.models import Sum
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

# Import models
from .models import (Account, BankTransaction, BankTransactionBill,
                     BankTransactionInvoice, Bill, FixedAsset, Invoice,
                     JournalEntry, JournalLine, Period)


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
        # call posting logic and update state
        je.transition_to("posted", user=user) # user=None means this parameter is optional
    return je


# ----------------------------
# Payment-related workflows
# ----------------------------
def apply_inv_payment(bt_id: int, invoice_id: int, amount: float):
    """
    Apply part (or all) of a bank transaction to an invoice.
    Locks both rows during the operation.
    """
    # Everything inside either succeeds
    # as one unit or rolls back if something fails
    with transaction.atomic():
        # Lock the bank transaction and invoice rows
        # until the transaction finishes
        bt = BankTransaction.objects.select_for_update().get(pk=bt_id)
        inv = Invoice.objects.select_for_update().get(pk=invoice_id)

        # Validate invoice outstanding
        if amount > inv.outstanding_amount:
            raise ValidationError("Payment exceeds invoice outstanding amount")

        # Validate bank transaction remaining capacity
        total_applied = bt.banktransactioninvoice_set.aggregate(
            total=models.Sum("applied_amount")
        )["total"] or Decimal("0")

        # Validation: prevent over-allocation
        if total_applied + amount > bt.amount:
            raise ValidationError(
                "Applied amounts exceed bank transaction amount")

        # Create join row
        """ This represents X amount of this
              bank transaction settles this invoice """

        # Create join record in M2M table
        BankTransactionInvoice.objects.create(
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


def apply_bank_tx_to_inv(bank_tx_id: int, invoice_applications: List[Dict]):
    with transaction.atomic():
        results = []
        for ap in invoice_applications:
            invoice_id = ap["invoice_id"]
            amount = ap["amount"]
            bt, inv = apply_inv_payment(bank_tx_id, invoice_id, amount)
            results.append((bt, inv))
        return results


def apply_bill_payment(bt_id: int, bill_id: int, amount: float):

    with transaction.atomic():

        bt = BankTransaction.objects.select_for_update().get(pk=bt_id)
        bill = Bill.objects.select_for_update().get(pk=bill_id)

        if amount > bill.outstanding_amount:
            raise ValidationError("Payment exceeds bill outstanding amount")

        total_applied = bt.banktransactionbill_set.aggregate(
            total=models.Sum("applied_amount")
        )["total"] or Decimal("0")

        if total_applied + amount > bt.amount:
            raise ValidationError(
                "Applied amounts exceed bank transaction amount")

        BankTransactionBill.objects.create(
            bank_transaction=bt,
            bill=bill,
            applied_amount=amount,
            company=bt.company,
        )

        bill.outstanding_amount -= amount
        if bill.outstanding_amount <= 0:
            bill.status = "paid"
        bill.save(update_fields=["outstanding_amount", "status"])

        total_applied += amount
        if total_applied >= bt.amount:
            bt.status = "fully_applied"
        else:
            bt.status = "partially_applied"
        bt.save(update_fields=["status"])

        return bt, bill


def apply_bank_tx_to_bill(bank_tx_id: int, bill_applications: List[Dict]):
    with transaction.atomic():
        results = []
        for ap in bill_applications:
            bill_id = ap["bill_id"]
            amount = ap["amount"]
            bt, bill = apply_bill_payment(bank_tx_id, bill_id, amount)
            results.append((bt, bill))
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

    # depreciation entry + lines are either all committed or all rolled back
    with transaction.atomic():
        # 1. Create JournalEntry header
        je_desc = f"Depreciation for asset {asset.asset_code or asset.id}"
        je = JournalEntry.objects.create(
            company=asset.company,
            period=period,
            date=timezone.now().date(),
            description=je_desc,
            status="draft",
            created_by=user,
            source_type="FixedAsset",  # link back to FixedAsset
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
    AccountBalanceSnapshot = apps.get_model(
        "accounts_core", "AccountBalanceSnapshot")

    # Loop over each child JournalLine
    # to update snapshot of corresponding account
    for line in journal.lines.all():
        # journal.journalline_set.all() works
        # because Django automatically gives you reverse relation manager
        # from (journal: JournalEntry) → JournalLine
        snapshot, _ = AccountBalanceSnapshot.objects.get_or_create(
            # Grab snapshot row for (company, account, date) or
            # create one if missing
            company=journal.company,
            account=line.account,
            snapshot_date=journal.date,
        )
        # Update debit/credit aggregates
        # Add this line’s debit/credit to running balance for that day
        # don’t try to add None
        snapshot.debit_balance += line.debit_local or 0
        snapshot.credit_balance += line.credit_local or 0
        snapshot.save()  # Save updated snapshot


# ----------------------------------------------
# Invoice status update & JE creation workflows
# ----------------------------------------------
"""Move invoice from draft → open (after validation)."""
def open_invoice(invoice: Invoice, user=None):
    if not invoice.lines.exists():
        raise ValidationError("Cannot open invoice with no lines")

    # idempotency: don't post twice
    if JournalEntry.objects.filter(source_type="invoice", source_id=invoice.pk).exists():
        # already posted; just transition status if needed
        invoice.transition_to("open")
        return invoice

    # create the invoice/revenue JE and transition in one atomic operation
    with transaction.atomic():
        create_invoice_journal(invoice, user=user)
        invoice.transition_to("open")
    return invoice

"""Move invoice from open → paid (only when outstanding == 0)."""
def pay_invoice(invoice: Invoice):
    # Avoid marking unpaid ones as paid
    if invoice.outstanding_amount != 0:
        raise ValidationError(
            "Cannot mark invoice as paid until fully settled")
    invoice.transition_to("paid")
    return invoice

def _get_ar_account_for_company(company):
    # Prefer Customer.default_ar_account if present, else fallback to a known control code like '1130'
    # Adjust this lookup to match data (Account with is_control_account True, code '1130', etc.)
    ar = Account.objects.filter(company=company, is_control_account=True, code__startswith="113").first()
    if not ar:
        raise ValidationError("No AR control account configured for company")
    return ar

def create_invoice_journal(invoice: Invoice, user=None) -> JournalEntry:
    """
    Create & post JE for invoice (revenue recognition).
    Produces:
      Debit: Accounts Receivable (control) = invoice.total
      Credit: Revenue accounts (per line) = amounts per line
    """
    if invoice.total <= 0:
        raise ValidationError("Invoice total must be > 0 to post revenue JE")

    # pick AR control account
    ar_account = invoice.customer.default_ar_account or _get_ar_account_for_company(invoice.company)

    # Prepare credits aggregated by line.account
    credits = {}
    for line in invoice.lines.all():
        if not line.account:
            raise ValidationError(f"InvoiceLine {line.pk} has no revenue account")
        credits.setdefault(line.account, Decimal("0.00"))
        credits[line.account] += line.line_total

    if not credits:
        raise ValidationError("Invoice has no lines to post")

    with transaction.atomic():
        je = JournalEntry.objects.create(
            company=invoice.company,
            date=invoice.date,
            reference=f"Inv {invoice.invoice_number or invoice.pk}",
            description=f"Invoice {invoice.invoice_number or invoice.pk}",
            status="draft",
            source_type="invoice",            
            source_id=invoice.pk,          
            created_by=user,
        )
        # Debit AR (single line)
        JournalLine.objects.create(
            company=invoice.company,
            journal=je,
            account=ar_account,
            description=f"AR for Invoice {invoice.invoice_number or invoice.pk}",
            currency=invoice.company.default_currency,
            debit_original=invoice.total,
            credit_original=Decimal("0.00"),
            invoice=invoice, 
        )
        # Credit revenue per account
        for acct, amt in credits.items():
            JournalLine.objects.create(
                company=invoice.company,
                journal=je,
                account=acct,
                description=f"Revenue: invoice {invoice.invoice_number or invoice.pk}",
                currency=invoice.company.default_currency,
                debit_original=Decimal("0.00"),
                credit_original=amt,
                invoice=invoice, 
            )
        # Post (this runs validations & marks lines posted)
        je.post(user=user)
    return je

def _resolve_bank_ledger_account(bank_account):
    """
    Return the ledger Account instance that represents the bank_account in the chart of accounts.
    Raises ValidationError if not resolvable.
    """
    # Prefer explicit FK added by migration: BankAccount.ledger_account
    ledger = getattr(bank_account, "ledger_account", None)
    if ledger:
        return ledger

    # Fallbacks:
    # - company default cash account
    # - look up by a known account code (e.g. '1010' Common cash)
    # - raise error so the caller can respond
    raise ValidationError(
        "BankAccount has no linked ledger 'Account'. Please set bank_account.ledger_account "
        "or provide a mapping function like _resolve_bank_ledger_account()."
    )


def create_payment_journal(bank_tx: BankTransaction, invoice: Invoice, amount: Decimal, user=None) -> JournalEntry:
    """
    Create & post payment JE: debit bank account, credit AR control account.
    This should be called inside the same transaction that creates BankTransactionInvoice.
    Idempotent-ish: check if a JE with same source_type/source_id and same fingerprint exists.
    """
    if amount <= 0:
        raise ValidationError("Applied amount must be positive")

    if bank_tx.company_id != invoice.company_id:
        raise ValidationError("Bank transaction and invoice must belong to same company")

    bank_account = bank_tx.bank_account
    if not bank_account:
        raise ValidationError("BankTransaction has no bank_account")
    
    # Resolve ledger Account for the BankAccount
    ledger_account = _resolve_bank_ledger_account(bank_tx.bank_account)
    if ledger_account is None:
        raise ValidationError("Cannot resolve ledger account for bank transaction's bank_account")


    ar_account = invoice.customer.default_ar_account or _get_ar_account_for_company(invoice.company)

    # idempotency: try to find existing JE matching this bank_tx + invoice + amount
    existing = JournalEntry.objects.filter(
        source_type='bank_transaction',
        source_id=bank_tx.pk,
        lines__invoice=invoice,
        lines__credit_original=amount,
    ).distinct()
    if existing.exists():
        return existing.first()
    
    with transaction.atomic():
        je = JournalEntry.objects.create(
            company=invoice.company,
            date=bank_tx.payment_date,
            reference=f"Payment BT:{bank_tx.pk} → Inv:{invoice.invoice_number or invoice.pk}",
            description=f"Payment for invoice {invoice.invoice_number or invoice.pk}",
            status="draft",
            source_type="bank_transaction",
            source_id=bank_tx.pk,
            created_by=user,
        )
        # Debit bank account
        JournalLine.objects.create(
            company=invoice.company,
            journal=je,
            account=bank_account.ledger_account,  # bank_account is an Account in some designs; adapt if BankAccount and Account are different
            description=f"Bank receipt for invoice {invoice.invoice_number or invoice.pk}",
            currency=invoice.company.default_currency,
            debit_original=amount,
            credit_original=Decimal("0.00"),
            bank_transaction=bank_tx,
            invoice=invoice,
        )
        # Credit AR
        JournalLine.objects.create(
            company=invoice.company,
            journal=je,
            account=ar_account,
            description=f"Clear AR for invoice {invoice.invoice_number or invoice.pk}",
            currency=invoice.company.default_currency,
            debit_original=Decimal("0.00"),
            credit_original=amount,
            invoice=invoice,
        )

        je.post(user=user)
    return je


def apply_payment_to_invoice(bank_tx_invoice: "BankTransactionInvoice", user=None) -> JournalEntry:
    """
    Apply a payment (BankTransactionInvoice instance) to its invoice.
    Creates JournalEntry (cash receipt), updates invoice outstanding/status,
    updates bank_transaction applied totals and status, and persists the BankTransactionInvoice link.
    Should be executed under transaction.atomic() and uses select_for_update for safety.
    Returns the created (or existing) JournalEntry.
    """
    from .models import BankTransaction, Invoice, BankTransactionInvoice  # avoid cyc import
    bt = bank_tx_invoice.bank_transaction
    inv = bank_tx_invoice.invoice
    amt = bank_tx_invoice.applied_amount

    if not bt or not inv:
        raise ValidationError("Both bank_transaction and invoice must be set")

    if amt is None or amt <= Decimal("0.00"):
        raise ValidationError("Applied amount must be positive")

    if bt.company_id != inv.company_id:
        raise ValidationError("Bank transaction and invoice must belong to same company")

    with transaction.atomic():
        # lock invoice row to avoid race conditions
        inv = Invoice.objects.select_for_update().get(pk=inv.pk)

        print("applied_amount:", amt)
        print("outstanding_amount:", inv.outstanding_amount)

        if amt > inv.outstanding_amount:
            raise ValidationError("Applied amount exceeds invoice outstanding")

        # persist the BankTransactionInvoice record if not saved
        if not bank_tx_invoice.pk:
            bank_tx_invoice.company = bank_tx_invoice.company or bt.company or inv.company
            bank_tx_invoice.save()

        # idempotency: don't apply twice
        existing_je = JournalEntry.objects.filter(
            source_type='bank_transaction',
            source_id=bt.pk,
            lines__invoice=inv,
            lines__credit_original=amt
        ).distinct()
        if existing_je.exists():
            je = existing_je.first()
        else:
            # create payment JE
            je = create_payment_journal(bt, inv, amt, user=user)

        # update invoice outstanding and status
        inv.outstanding_amount = (inv.outstanding_amount - amt).quantize(Decimal("0.01"))
        if inv.outstanding_amount <= Decimal("0.00"):
            inv.status = "paid"
            inv.outstanding_amount = Decimal("0.00")
        else:
            inv.status = "partially_paid"
        inv.save(update_fields=['outstanding_amount', 'status'])

        # update bank transaction applied_total and status
        # assume BankTransaction has methods applied_total() and a status field
        bt = BankTransaction.objects.select_for_update().get(pk=bt.pk)
        # Cached column check
        if hasattr(bt, 'applied_total_cached'):
            bt.applied_total_cached = bt.applied_total_cached + amt
        # Set status by comparing applied_total() vs amount_received or bank_tx.amount
        total_applied = getattr(bt, 'applied_total', lambda: None)()
        # If applied_total() is a method and returns None when not implemented, compute by DB:
        if total_applied is None:
            total_applied = BankTransactionInvoice.objects.filter(bank_transaction=bt).aggregate(
                total=Coalesce(Sum('applied_amount'), Decimal('0.00'))
            )['total']
        # Set status
        if total_applied >= getattr(bt, 'amount', total_applied):
            bt.status = 'fully_applied'
        elif total_applied > Decimal("0.00"):
            bt.status = 'partially_applied'
        else:
            bt.status = 'unapplied'
        bt.save(update_fields=['status', 'applied_total_cached'] if hasattr(bt, 'applied_total_cached') else ['status'])

        # mark BankTransactionInvoice as applied/persisted fields
        bank_tx_invoice.journal_entry = je  # keep reference
        bank_tx_invoice.save(update_fields=['journal_entry'])  # plus any status fields

    return je

# ------------------------------------
# Bill status update workflows
# ------------------------------------
"""Move bill from draft → posted (after validation)."""


def post_bill(bill: Bill):
    if not bill.lines.exists():
        raise ValidationError("Cannot post bill with no lines")
    bill.transition_to("posted")
    return bill


"""Move bill from posted → paid (only when outstanding == 0)."""


def pay_bill(bill: Bill):
    if bill.outstanding_amount != 0:
        raise ValidationError("Cannot mark bill as paid until fully settled")
    bill.transition_to("paid")
    return bill


# ----------------------------------------
# Bank Transaction status update workflows
# ----------------------------------------

""" Apply payment to invoice and update transition statuses safely """


def pay_inv_and_update_status(bt_id, invoice_id, amount):

    with transaction.atomic():
        bt, inv = apply_inv_payment(bt_id, invoice_id, amount)

        """ update invoice status if needed """
        if inv.outstanding_amount == 0:
            inv.transition_to("paid")
        elif inv.status == "draft":
            inv.transition_to("open")

        """  update bank transaction status """
        # Call BankTransaction.applied_total()
        # to find how much of this transaction has been applied to invoices
        if bt.applied_total() == 0:
            bt.transition_to("unapplied")
        elif bt.applied_total() < bt.amount:
            bt.transition_to("partially_applied")
        else:
            bt.transition_to("fully_applied")

        return bt, inv


""" Apply payment to bill and update transition statuses safely """


def pay_bill_and_update_status(bt_id, bill_id, amount):

    with transaction.atomic():
        bt, bill = apply_bill_payment(bt_id, bill_id, amount)

        """ update bill status if needed """
        if bill.outstanding_amount == 0:
            bill.transition_to("paid")
        elif bill.status == "draft":
            bill.transition_to("posted")

        """  update bank transaction status """
        # Call BankTransaction.applied_total()
        # to find how much of this transaction has been applied to bills
        if bt.applied_total() == 0:
            bt.transition_to("unapplied")
        elif bt.applied_total() < bt.amount:
            bt.transition_to("partially_applied")
        else:
            bt.transition_to("fully_applied")

        return bt, bill


# ------------------------------------
# Posting Account Validation workflows
# ------------------------------------


def post_invoice_to_journal(invoice: Invoice, user=None):
    ar_account = invoice.customer.default_ar_account
    if not ar_account or not ar_account.is_control_account:
        raise ValidationError("Customer must have a valid AR control account.")

    # Create JournalEntry
    journal = JournalEntry.objects.create(
        company=invoice.company,
        date=invoice.date,
        reference=invoice.invoice_number,
        description=f"Invoice {invoice.invoice_number}",
        created_by=user,
        status="draft",
    )

    # Debit AR control account
    JournalLine.objects.create(
        journal=journal,
        company=invoice.company,
        account=ar_account,
        debit_amount=invoice.total,
        credit_amount=Decimal("0.00"),
    )

    # Credit revenue accounts from lines
    for line in invoice.lines.all():
        if not line.account or line.account.ac_type != "Income":
            raise ValidationError(
                "Invoice line must point to an Income account.")
        JournalLine.objects.create(
            journal=journal,
            company=invoice.company,
            account=line.account,
            debit_amount=Decimal("0.00"),
            credit_amount=line.line_total,
        )
    return journal


def post_bill_to_journal(bill: Bill, user=None):
    with transaction.atomic():
        ap_account = bill.vendor.default_ap_account
        if not ap_account or not ap_account.is_control_account:
            raise ValidationError(
                "Vendor must have a valid AP control account.")

        journal = JournalEntry.objects.create(
            company=bill.company,
            date=bill.date,
            reference=bill.bill_number,
            description=f"Bill {bill.bill_number}",
            created_by=user,
            status="draft",
        )

        # Debit expenses account from lines
        for line in bill.lines.all():
            if not line.account or line.account.ac_type != "Expense":
                raise ValidationError(
                    "Bill line must point to an Expense account.")
            JournalLine.objects.create(
                journal=journal,
                company=bill.company,
                account=line.account,
                debit_amount=line.line_total,
                credit_amount=Decimal("0.00"),
            )

        # Credit AP control account
        JournalLine.objects.create(
            journal=journal,
            company=bill.company,
            account=ap_account,
            debit_amount=Decimal("0.00"),
            credit_amount=bill.total,
        )

        bill.status = "posted"  # finalized & journal entry created
        bill.save(update_fields=["status"])

        return journal


def post_fixed_asset_to_journal(asset, user=None):
    with transaction.atomic():
        if not asset.account or asset.account.ac_type != "Asset":
            raise ValidationError(
                "Fixed asset must be linked " "to an Asset account.")

        journal = JournalEntry.objects.create(
            company=asset.company,
            date=asset.purchase_date,
            reference=f"FA-{asset.id}",
            description=f"Fixed asset purchase: {asset.name}",
            created_by=user,
            status="draft",
        )

        # Debit the asset account
        JournalLine.objects.create(
            journal=journal,
            company=asset.company,
            account=asset.account,
            debit_amount=asset.purchase_cost,
            credit_amount=Decimal("0.00"),
        )

        # Credit AP or Bank
        if asset.vendor and asset.vendor.default_ap_account:
            credit_account = asset.vendor.default_ap_account
        elif asset.bank_account:
            credit_account = asset.bank_account.account
        else:
            raise ValidationError(
                "Fixed asset must specify vendor (AP) or bank account."
            )

        JournalLine.objects.create(
            journal=journal,
            company=asset.company,
            account=credit_account,
            debit_amount=Decimal("0.00"),
            credit_amount=asset.purchase_cost,
        )

        asset.status = "capitalized"  # update lifecycle state
        asset.save(update_fields=["status"])

        return journal
