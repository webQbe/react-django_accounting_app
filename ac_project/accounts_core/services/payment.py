from decimal import Decimal
from typing import Dict, List
from django.db.models.functions import Coalesce
from django.db.models import Sum
from django.core.exceptions import ValidationError
from django.db import models, transaction
# Import models
from ..models import (BankTransaction, BankTransactionBill,
                     BankTransactionInvoice, Bill, Invoice,
                     JournalEntry,)
from .posting import create_payment_journal
from .audit_helper import log_action


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
    

def apply_payment_to_invoice(bank_tx_invoice: "BankTransactionInvoice", user=None) -> JournalEntry:
    """
    Apply a payment (BankTransactionInvoice instance) to its invoice.
    Creates JournalEntry (cash receipt), updates invoice outstanding/status,
    updates bank_transaction applied totals and status, and persists the BankTransactionInvoice link.
    Should be executed under transaction.atomic() and uses select_for_update for safety.
    Returns the created (or existing) JournalEntry.
    """
    from ..models import BankTransaction, Invoice, BankTransactionInvoice  # avoid cyc import
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

        # AUDIT LOGS
        log_action(
            action="apply_payment",
            instance=bank_tx_invoice,
            user=user,
            changes={
                "invoice_id": inv.pk,
                "bank_transaction_id": bt.pk,
                "amount": str(amt),
            },
        )

        log_action(
            action="update",
            instance=inv,
            user=user,
            changes={
                "outstanding_amount": str(inv.outstanding_amount),
                "status": inv.status,
            },
        )

        log_action(
            action="update",
            instance=bt,
            user=user,
            changes={
                "applied_total": str(bt.applied_total()),
                "status": bt.status,
            },
        )

        # mark BankTransactionInvoice as applied/persisted fields
        bank_tx_invoice.journal_entry = je  # keep reference
        bank_tx_invoice.save(update_fields=['journal_entry'])  # plus any status fields

    return je


