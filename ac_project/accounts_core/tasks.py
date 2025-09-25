from celery import shared_task
from django.db import models
from django.utils import timezone


@shared_task  # register this function as a Celery task
def recompute_all_snapshots(company_id):
    # import models lazily to avoid circular imports at module import time
    from .models import Account, AccountBalanceSnapshot, JournalLine

    # Fetch company’s accounts, each one will get a fresh balance snapshot
    company_accounts = Account.objects.filter(company_id=company_id)
    # Wipe out any previous snapshots for this company
    AccountBalanceSnapshot.objects.filter(company_id=company_id).delete()

    # Sum all debit and credit lines for this account
    for account in company_accounts:
        # To prevent 'or' from being applied inside aggregate() accidentally
        # Compute agg with Sum(...) first
        agg = JournalLine.objects.filter(account=account).aggregate(
            debit=models.Sum("debit_amount"),
            credit=models.Sum("credit_amount"),
        )
        # agg looks like:
        # {"debit": Decimal("1500.00"), "credit": Decimal("750.00")}
        # If nothing was posted, Django returns None → so fallback to 0

        # Write a new snapshot row for this account
        AccountBalanceSnapshot.objects.create(
            company=account.company,
            account=account,
            # Capture balances at this moment in time
            snapshot_date=timezone.now().date(),
            debit_balance=agg["debit"] or 0,
            credit_balance=agg["credit"] or 0,
        )
