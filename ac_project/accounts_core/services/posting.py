from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction

# Import models
from ..models import (Account, BankTransaction, Invoice,
                     JournalEntry, JournalLine)

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
