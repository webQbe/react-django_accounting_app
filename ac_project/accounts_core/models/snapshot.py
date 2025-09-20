from django.db import models   # ORM base classes to define database tables as Python classes
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from ..managers import TenantManager
from .entitymembership import Company
from .account import Account


# ---------- Account Balance Snapshot (optional materialized) ----------
class AccountBalanceSnapshot(models.Model): # Summary / materialized snapshot used for reporting performance
   
    # Tied to a specific tenant (multi-company setup)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Snapshot is for a specific GL account (like Cash, Accounts Payable, Sales)
    # on_delete= CASCADE: If you delete an Account, you don’t need orphaned snapshots floating around
    account = models.ForeignKey(Account, on_delete=models.CASCADE) 
    # The date snapshot is taken (daily, monthly, or at reporting cutoffs (e.g., end of period))
    snapshot_date = models.DateField()

    # Hold account balance split into debit/credit buckets
    debit_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    credit_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    """ Example:
            Cash account might show Debit = 10,000; Credit = 0.
            Accounts Payable might show Debit = 0; Credit = 5,000. """
    
    # Enforce tenant scoping
    objects = TenantManager() 
    
    class Meta:
        # Optimize queries like: “Get all account balances for Company A on 2025-08-31.”
        indexes = [models.Index(fields=["company", "snapshot_date"])]

        constraints = [
            # Ensure balances are never negative 
            # unless your reporting intentionally allows negatives
            models.CheckConstraint(
                condition=(
                    models.Q(debit_balance__gte=0) & 
                    models.Q(credit_balance__gte=0)
                ),
                name="ab_snap_non_negative_amounts"
            ),
            # Ensure you don’t store duplicate snapshots for the same account/date
            models.UniqueConstraint(
                                    fields=["company", "account", "snapshot_date"], 
                                    name="uq_company_account_snapshot_date"
                                )
        ]

    # Show company.slug, snapshot_date, account.code, account.name, and
    # debit/credit balances in admin dropdowns and debug logs 
    def __str__(self):
        return f"{self.company.slug} {self.snapshot_date} | {self.account.code} {self.account.name}: D {self.debit_balance} / C {self.credit_balance}"

    def clean(self):
        # Tenancy check
        # Ensure account chosen belongs to the same company
        if self.account and self.account.company != self.company:
            raise ValidationError("Account must belong to the same company.")
        
    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
    