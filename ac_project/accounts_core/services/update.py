from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
# Import models
from ..models import (Bill, Invoice, JournalEntry)
from .posting import create_invoice_journal
from .payment import apply_inv_payment, apply_bill_payment
from .audit_helper import log_action


# ----------------------------------------------
# Invoice status update workflows
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
        # Log Invoice creation
        log_action(
            action="create",
            instance=invoice,
            user=user,
            changes={"total": str(invoice.total)},
        )
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