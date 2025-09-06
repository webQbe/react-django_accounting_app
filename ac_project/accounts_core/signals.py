
from django.db.models.signals import pre_delete 
from django.dispatch import receiver
from django.core.exceptions import ValidationError 

from .models import (
    BankTransactionInvoice, Invoice, 
    BankTransactionBill, Bill, Account,
    JournalEntry, JournalLine, Period
)

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
        raise ValidationError("Cannot delete a period with posted journal entries.")