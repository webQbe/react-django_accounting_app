from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from .models import (Account, BankTransactionBill, BankTransactionInvoice,
                     Bill, Invoice, InvoiceLine, JournalEntry, JournalLine,
                     Period)

""" Block invoice deletion if any payments are applied."""


# pre_delete signal auto-fires just before Django deletes a model instance
# it’s connected to the Invoice model
@receiver(pre_delete, sender=Invoice)
# Receiver function receives instance (Invoice being deleted)
def prevent_delete_invoice_with_payments(sender, instance, **kwargs):
    # Check if any BankTransactionInvoice rows point to this invoice exist
    if BankTransactionInvoice.objects.filter(invoice=instance).exists():
        # prevent delete
        raise ValidationError("Cannot delete invoice with applied payments.")


"""
    Recalculate invoice totals when a line is added/updated/removed.
    Use update via model methods to keep validation/consistency.
"""


@receiver((post_save, post_delete), sender=InvoiceLine)
def invoice_line_changed(sender, instance, **kwargs):
    try:
        inv = Invoice.objects.get(pk=instance.invoice_id)
    except Invoice.DoesNotExist:
        return
    # recompute and save only the changed fields to reduce churn
    inv.recalc_totals()
    # save totals, no need to revalidate lines here
    inv.save(update_fields=["total", "outstanding_amount"])


"""Block bill deletion if any payments are applied."""


@receiver(pre_delete, sender=Bill)
def prevent_delete_bill_with_payments(sender, instance, **kwargs):
    if BankTransactionBill.objects.filter(bill=instance).exists():
        raise ValidationError("Cannot delete bill with applied payments.")


"""Block deletion if account has ever been used in a journal line."""


@receiver(pre_delete, sender=Account)
def prevent_delete_account_with_journal_lines(sender, instance, **kwargs):
    if JournalLine.objects.filter(account=instance).exists():
        raise ValidationError("Cannot delete account used in journal lines.")


"""Block deletion if period has posted journals."""


@receiver(pre_delete, sender=Period)
def prevent_delete_period_with_posted_journals(sender, instance, **kwargs):
    if JournalEntry.objects.filter(period=instance, status="posted").exists():
        raise ValidationError(
            "Cannot delete a period with posted journal entries.")
