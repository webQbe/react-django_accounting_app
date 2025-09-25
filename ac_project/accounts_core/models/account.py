from django.core.exceptions import ValidationError
from django.db import models
from ..managers import TenantManager
from .ac_category import AccountCategory
from .entitymembership import Company

# Choice Lists
AC_TYPES = [
    # Used in Account model to classify general ledger accounts
    ("asset", "Asset"),
    ("liability", "Liability"),
    ("equity", "Equity"),
    ("income", "Income"),
    ("expense", "Expense"),
]

# Define whether the account normally increases
# on the debit side or credit side
NORMAL_BALANCE = [
    ("debit", "Debit"),
    ("credit", "Credit"),
]


class Account(models.Model):
    """
    Actual ledger account entry in Chart of Accounts.
    - code should be unique per company
    - ac_type: determines reporting -BS vs P&L
    - normal_balance: used to interpret sign when building reports
    """

    company = models.ForeignKey(  # Each account belongs to one company
        Company,  # All reports must filter by company_id to prevent data leaks
        on_delete=models.CASCADE,
    )
    # Every account has a code
    # which lets you sort/group accounts consistently in reports.
    code = models.CharField(
        max_length=32
    )
    name = models.CharField(
        max_length=200
    )  # Human-readable name → "Cash on Hand", "Accounts Payable".

    # Classify account into one of the 5 basic accounting types
    ac_type = models.CharField(
        max_length=10,
        choices=AC_TYPES,  # types Asset, Liability, Equity, Income, Expense.
        # This tells system whether the account
        # goes on the Balance Sheet or P&L
    )

    # Define whether the account normally carries a debit or credit balance
    normal_balance = models.CharField(
        max_length=6,
        choices=NORMAL_BALANCE,
        default="debit",
        # Assets/Expenses → Debit, Liabilities/Equity/Income → Credit.
    )
    # Optional hierarchy:
    # you can make sub-accounts
    # (e.g. 1000 Cash, 1001 Petty Cash, 1002 Bank Account)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        # you can’t delete a parent if children exist
    )

    category = (
        # Groups an account under a reporting category (optional)
        models.ForeignKey(
            # E.g., "Bank Account" could belong to "Current Assets".
            AccountCategory,
            null=True,
            blank=True,
            on_delete=models.SET_NULL,
        )
    )
    # “soft deactivate” accounts (hide in UI, stop new postings)
    # without deleting history
    is_active = models.BooleanField(
        default=True
    )
    created_at = models.DateTimeField(
        auto_now_add=True
    )  # Track when the account was created.
    is_control_account = models.BooleanField(
        default=False
    )  # marker for accounts that must reconcile with subledgers

    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:
        indexes = [  # Optimize queries
            # For reports grouped by ac_type
            # (Trial Balance, P&L, Balance Sheet)
            models.Index(
                fields=["company", "ac_type"]
            ),
            # For looking up accounts by code
            models.Index(fields=["company", "code"]),
            models.Index(
                fields=["company", "parent"]
            ),  # Sub-accounts by parent account
        ]

        """ Each company defines its own chart of accounts.
               Codes repeat across companies but must be unique within one. """
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="uq_company_account_code"
            )
        ]

    def __str__(self):
        # Make accounts readable in the Django admin and debugging
        return f"{self.company.slug}:{self.code} – {self.name}"
        # Example: "acme:1000 – Cash on Hand".

    def clean(self):
        """Enforce company consistency (multi-tenancy)"""
        # Check if category belongs to same company
        if self.category and self.category.company != self.company:
            raise ValidationError(
                "AccountCategory must belong to the same company as Account."
            )

        # Check if parent account belongs to same company
        if self.parent and self.parent.company != self.company:
            raise ValidationError(
                "Parent & child accounts must belong to the same company"
            )

    def save(self, *args, **kwargs):
        """Enforce business immutability
        (can’t disable accounts used in journal lines)"""
        # Prevent disabling if used in journals
        if not self.pk:
            # If no primary key → this is a new object →
            # just save (no need for checks)
            return super().save(*args, **kwargs)
        # Fetch the previous version of account from DB
        old = Account.objects.filter(pk=self.pk).first()

        # If account was active before, but now being set to inactive
        if old and old.is_active and not self.is_active:
            from .journal import JournalLine

            # check usage (referenced in transactions)
            used = JournalLine.objects.filter(account=self).exists()
            if used:  # if referenced prevent from deactivating account
                raise ValidationError(
                    "Cannot disable an account that is used in journal lines."
                )
        return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)
