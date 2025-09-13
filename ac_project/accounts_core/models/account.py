from django.db import models        # ORM base classes to define database tables as Python classes
from .entitymembership import Company 
from ..managers import TenantManager
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from .ac_category import AccountCategory
from .journal import JournalLine

# Choice Lists
AC_TYPES = [
    # Used in Account model to classify general ledger accounts
    ("asset", "Asset"),
    ("liability", "Liability"),
    ("equity", "Equity"),
    ("income", "Income"),
    ("expense", "Expense"),
]

NORMAL_BALANCE = [
    # Defines whether the account normally increases on the debit side  or credit side
    ("debit", "Debit"),
    ("credit", "Credit"),
]

class Account(models.Model): # Actual ledger account entry in Chart of Accounts
    """
    Chart of Accounts entry.
    - code should be unique per company
    - ac_type: determines reporting -BS vs P&L
    - normal_balance: used to interpret sign when building reports
    """
    company = models.ForeignKey( # Each account belongs to one company
                                 Company, # All reports must filter by company_id to prevent data leaks
                                 on_delete=models.CASCADE 
                                ) 
    
    code = models.CharField(max_length=32)  # Every account has a code which lets you sort/group accounts consistently in reports.
    name = models.CharField(max_length=200) # Human-readable name → "Cash on Hand", "Accounts Payable".
    
    ac_type = models.CharField( # Classify  account into one of the 5 basic accounting types
                                max_length=10, 
                                choices=AC_TYPES # types Asset, Liability, Equity, Income, Expense.
                                # This tells system whether the account goes on the Balance Sheet or P&L
                              ) 
    
    normal_balance = models.CharField( # Define whether the account normally carries a debit or credit balance
                                        max_length=6, 
                                        choices=NORMAL_BALANCE, # Assets/Expenses → Debit, Liabilities/Equity/Income → Credit.
                                        default="debit"
                                    )
    
    parent = models.ForeignKey( # Optional hierarchy: you can make sub-accounts (e.g. 1000 Cash, 1001 Petty Cash, 1002 Bank Account)
                                "self", null=True, blank=True, 
                                on_delete=models.PROTECT # you can’t delete a parent if children exist
                              )
    
    category = models.ForeignKey( # Groups an account under a reporting category (optional)
                                    AccountCategory, # E.g., "Bank Account" could belong to "Current Assets".
                                    null=True, blank=True, 
                                    on_delete=models.SET_NULL
                                )
    
    is_active = models.BooleanField(default=True) # “soft deactivate” accounts (hide in UI, stop new postings) without deleting history
    created_at = models.DateTimeField(auto_now_add=True) # Track when the account was created.
    is_control_account = models.BooleanField(default=False) # marker for accounts that must reconcile with subledgers
    
    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        indexes = [ # Optimize queries
            models.Index(fields=["company", "ac_type"]), # For reports grouped by ac_type (Trial Balance, P&L, Balance Sheet)
            models.Index(fields=["company", "code"]),    # For looking up accounts by code
            models.Index(fields=["company", "parent"]),  # Sub-accounts by parent account
        ]

        """ Each company defines its own chart of accounts. 
               Codes repeat across companies but must be unique within one. """
        constraints = [
            models.UniqueConstraint(
                            fields=["company", "code"], 
                            name="uq_company_account_code"
                    )]

    def __str__(self): 
        # Make accounts readable in the Django admin and debugging
        return f"{self.company.slug}:{self.code} – {self.name}" 
        # Example: "acme:1000 – Cash on Hand".

    def clean(self):
        """ Enforce company consistency (multi-tenancy) """
        # Check if category belongs to same company
        if self.category and self.category.company != self.company:
            raise ValidationError("AccountCategory must belong to the same company as Account.")

        # Check if parent account belongs to same company
        if self.parent and self.parent.company != self.company:
            raise ValidationError("Parent account must belong to the same company as child account.")

    def save(self, *args, **kwargs):
        """ Enforce business immutability (can’t disable accounts used in journal lines) """
        # Prevent disabling if used in journals
        if not self.pk: 
            # If no primary key → this is a new object → just save (no need for checks)
            return super().save(*args, **kwargs)
        # Fetch the previous version of account from DB
        old = Account.objects.filter(pk=self.pk).first()

        # If account was active before, but now being set to inactive
        if old and old.is_active and not self.is_active:
            # check usage (referenced in transactions)
            used = JournalLine.objects.filter(account=self).exists()
            if used: # if referenced prevent from deactivating account 
                raise ValidationError("Cannot disable an account that is used in journal lines.")
        return super().save(*args, **kwargs)
    
