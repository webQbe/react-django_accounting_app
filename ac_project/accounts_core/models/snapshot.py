from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import models
from ..managers import TenantManager
from .account import Account
from .entitymembership import Company


# ---------- Account Balance Snapshot (optional materialized) ----------
class AccountBalanceSnapshot(
    models.Model
):  # Summary / materialized snapshot used for reporting performance

    # Tied to a specific tenant (multi-company setup)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Snapshot is for a specific GL account
    # (like Cash, Accounts Payable, Sales)
    # on_delete= CASCADE: If you delete an Account,
    # you don’t need orphaned snapshots floating around
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    # The date snapshot is taken
    # (daily, monthly, or at reporting cutoffs (e.g., end of period))
    snapshot_date = models.DateField()
    # Hold account balance split into debit/credit buckets
    debit_balance = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    credit_balance = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal("0.00")
    )
    """ Example:
            Cash account might show Debit = 10,000; Credit = 0.
            Accounts Payable might show Debit = 0; Credit = 5,000. """

    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:
        # Optimize queries like:
        # “Get all account balances for Company A on 2025-08-31.”
        indexes = [models.Index(fields=["company", "snapshot_date"])]

        constraints = [
            # Ensure balances are never negative
            # unless your reporting intentionally allows negatives
            models.CheckConstraint(
                condition=(
                    models.Q(debit_balance__gte=0) &
                    models.Q(credit_balance__gte=0)
                ),
                name="ab_snap_non_negative_amounts",
            ),
            # Do not store duplicate snapshots for the same account/date
            models.UniqueConstraint(
                fields=["company", "account", "snapshot_date"],
                name="uq_company_account_snapshot_date",
            ),
        ]

    # Show company.slug, snapshot_date, account.code, account.name, and
    # debit/credit balances in admin dropdowns and debug logs
    def __str__(self):
        slug = self.company.slug
        snpd = self.snapshot_date
        acc = self.account.code
        acn = self.account.name
        db = self.debit_balance
        cb = self.credit_balance
        return f"{slug} {snpd} | {acc} {acn}: D {db} / C {cb}"

    def clean(self):
        # Tenancy check
        # Ensure account chosen belongs to the same company
        ac = self.account
        if ac and ac.company != self.company:
            raise ValidationError("Account must belong to the same company.")

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
