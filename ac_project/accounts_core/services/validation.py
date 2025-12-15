from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models, transaction
# Import models
from ..models import (Bill, Invoice,
                     JournalEntry, JournalLine)


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
