from django.db import models        # ORM base classes to define database tables as Python classes
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from django.conf import settings    # To access global project settings
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from django.db import models, transaction           # To wrap operations in a DB transaction
from django.utils import timezone                   # Timezone-aware datetime helper

# Choice Lists
AC_TYPES = [
    """ Used in Account model to classify general ledger accounts 
        First element = DB value (e.g., "asset"). Second element = human-readable label (e.g., "Asset")."""
    ("asset", "Asset"),
    ("liability", "Liability"),
    ("equity", "Equity"),
    ("income", "Income"),
    ("expense", "Expense"),
]

NORMAL_BALANCE = [
    """ Defines whether the account normally increases on the debit side (Assets, Expenses) or 
        credit side (Liabilities, Equity, Income)."""
    ("debit", "Debit"),
    ("credit", "Credit"),
]

JOURNAL_STATUS = [
    """ Used in JournalEntry.
        "draft" = still editable, "posted" = finalized, locked, "reversed" = reversal entry applied."""
    ("draft", "Draft"),
    ("posted", "Posted"),
    ("reversed", "Reversed"),
]

PAYMENT_METHODS = [
    """ Used in BankTransaction or Payment entities.
        Keeps payment method standardized across records. """
    ("cash", "Cash"),
    ("cheque", "Cheque"),
    ("bank_transfer", "Bank Transfer"),
    ("card", "Card"),
    ("other", "Other"),
]

# ---------- Tenant / Company ----------
class Company(models.Model):
    """Tenant / Organization""" 
    name = models.CharField(max_length=200) # Store company’s full display name

    slug = models.SlugField( # A URL-friendly identifier
                            max_length=80, 
                            unique=True  # no two companies can have the same slug
                            ) 
    
    owner = models.ForeignKey(  # Links to a user account (creator or admin of company)
                    settings.AUTH_USER_MODEL,  # use user model project is configured with
                    null=True, blank=True,     # optional field
                    on_delete=models.SET_NULL  # if user is deleted, company record stays, but owner is set to NULL.
                )
    
    # Store default currency
    """ Important since all journal entries and invoices need to know which currency they belong to """
    currency_code = models.CharField(max_length=10, default="USD") 

    # Store timestamp when the record is first created
    created_at = models.DateTimeField(auto_now_add=True)

    # Meta options
    class Meta:
        verbose_name_plural = "companies"

    # String Representation
    def __str__(self):
        return self.name


# ---------- Chart of Accounts ----------
class AccountCategory(models.Model): # For organizing accounts into categories
    company = models.ForeignKey(Company, on_delete=models.CASCADE) # each company has its own set of categories (multi-tenant safe)
    name = models.CharField(max_length=100) # category’s label (e.g. "Current Assets")

    class Meta:
        unique_together = ("company", "name") # A company can’t have two categories with the same name

    def __str__(self):
        return f"{self.company.slug} - {self.name}" # Example: "acme - Current Assets"

class Account(models.Model): # Actual ledger account entry in Chart of Accounts
    """
    Chart of Accounts entry.
    - code should be unique per company
    - ac_type: determines reporting (BS vs P&L)
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

    class Meta:
        unique_together = ("company", "code") # Enforce unique account codes per company
        """ Two companies can both have an account "1000 Cash", but the same company cannot. """
        indexes = [ # Optimize queries
            models.Index(fields=["company", "ac_type"]), # For reports grouped by ac_type (Trial Balance, P&L, Balance Sheet)
            models.Index(fields=["company", "code"]),    # For looking up accounts by code
        ]

    def __str__(self): 
        # Make accounts readable in the Django admin and debugging
        return f"{self.company.slug}:{self.code} – {self.name}" 
        # Example: "acme:1000 – Cash on Hand".
    

# ---------- Period (accounting period) ----------
class Period(models.Model): # Each Period represents a time bucket during which financial transactions are grouped
   
    # Every company has its own independent calendar of periods
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    """ 
        Tenant isolation: 
        "Company A" can close July while "Company B" is still open. 
    """

    # Human-readable label for the period
    name = models.CharField(max_length=50)  # Example: "2025-Q3" or "FY2025-01"

    # Define the exact date range of the accounting period
    start_date = models.DateField()
    end_date = models.DateField()

    # Indicate whether the books for this period are closed
    is_closed = models.BooleanField(default=False)
    """ 
        When is_closed=True:
            No new postings allowed.
            Prevents backdating transactions that could corrupt finalized reports.
    """

    class Meta:
        # Prevent duplicate period names inside the same company
        unique_together = ("company", "name") # E.g., "2025-07" can exist once per company
        
        # Default query ordering: periods are returned sorted by company, then chronologically
        ordering = ("company", "start_date") # no need to sort manually

    def __str__(self):
        return f"{self.company.slug} {self.name}" # Example: "acme 2025-07".
