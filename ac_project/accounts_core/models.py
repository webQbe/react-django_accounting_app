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


# ---------- Customers & Vendors ----------
class Customer(models.Model): # Represents client who receives invoices (AR side)

    # Multi-tenant: every customer belongs to a single company.
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    """ Example: 
        Acme Ltd (Company A) can have its own customers separate from Beta Inc (Company B). 
    """

    # The customer’s legal or trade name
    name = models.CharField(max_length=200)

    # Optional contact for billing/communication
    contact_email = models.EmailField(null=True, blank=True)

    # Standard credit terms
    payment_terms_days = models.IntegerField(default=30)
    """ Example: If terms = 30 → invoice due 30 days after issue. """

    # FK to the Accounts Receivable account in Chart of Accounts
    """ If set: when creating an invoice for this customer, 
        the system automatically books AR lines to that account. 
    """
    default_ar_account = models.ForeignKey(
                            Account, 
                            null=True, blank=True, 

                            # If AR account is deleted/disabled, customer record isn’t broken, it just loses its default AR link.
                            on_delete=models.SET_NULL,

                            # Make reverse lookups possible
                            related_name="customers_default_ar"
                            """ (i.e., which customers use this AR account as default). """
                        )

    class Meta:
        # Enforce uniqueness per tenant
        unique_together = ("company", "name") 
        """ Acme Ltd can have a customer named "ABC Trading", and 
            Beta Inc can also have a customer with the same name. """

    # Display customer name in admin/UI
    def __str__(self):
        return self.name


class Vendor(models.Model):  # Mirrors Customer but for Accounts Payable (AP)
    
    company = models.ForeignKey(Company, on_delete=models.CASCADE) # Multi-tenant

    # Same fields as Customer, but now for suppliers/vendors
    name = models.CharField(max_length=200)
    contact_email = models.EmailField(null=True, blank=True)
    payment_terms_days = models.IntegerField(default=30)

    # FK to the Accounts Payable account in Chart of Accounts
    """ If set: when creating a Bill for this vendor, 
        the system books AP lines to this account. """
    default_ap_account = models.ForeignKey(
                                Account, 
                                null=True, blank=True, 
                                on_delete=models.SET_NULL,
                                related_name="vendors_default_ap" # Lets you see which vendors use a given AP account
                            )

    class Meta:
        # Vendor names must be unique per company
        unique_together = ("company", "name")

    def __str__(self):
        return self.name

# ---------- Items (optional product/service) ----------
class Item(models.Model): # Represents something a company sells & purchases

    # Multi-tenant: each item belongs to a company
    company = models.ForeignKey( Company, 
                                # If the company is deleted, its items are deleted too (CASCADE)
                                on_delete=models.CASCADE
                            )
    # Stock Keeping Unit (optional unique code per item)
    sku = models.CharField( 
                            max_length=80, 
                            null=True, blank=True # SKU is optional, useful if the business only sells services.
                        )
    
    # Required human-readable name of the item
    name = models.CharField(max_length=200) 

    # FK → Revenue account (Chart of Accounts)
    """ Example: Item "Web Hosting" → posts to "4000: Sales Revenue". """
    sales_account = models.ForeignKey( 
                                        Account, # If set, invoices for this item auto-post revenue lines to this account.
                                        null=True, blank=True, 
                                        on_delete=models.SET_NULL,
                                        related_name="items_sales_account"
                                    )
    
    # FK → Expense account (for purchases/bills)
    """ Example: "Printer Paper" → posts to "6000: Office Supplies Expense". """
    purchase_account = models.ForeignKey(  Account, 
                                            null=True, blank=True, 
                                            # if the linked account is deleted, the item stays but without a default account
                                            on_delete=models.SET_NULL, 
                                            related_name="items_purchase_account"
                                        )
    
    # Current stock level of the item
    on_hand_qty = models.DecimalField(  
                                        max_digits=14, decimal_places=4, # Allow precise tracking (supports large quantities with fractional amounts, e.g. liters).
                                        default=Decimal("0.0")           # Default = 0
                                    )

    class Meta:
        unique_together = ("company", "sku")                 # Ensure each SKU is unique within a company
        indexes = [models.Index(fields=["company", "name"])] # for fast lookups (e.g. autocomplete when searching items)

    def __str__(self):
        return self.name


# ---------- Journal (Header) & JournalLine ----------
class JournalEntry(models.Model): # Represents one accounting transaction 
    # Header-level info
    # Multi-tenant: every entry belongs to a company
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Optional link to an accounting period (for reporting, closing)
    period = models.ForeignKey(Period, null=True, blank=True, on_delete=models.SET_NULL)
    # Business metadata
    date = models.DateField()
    reference = models.CharField(max_length=200, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    status = models.CharField(
                        max_length=10, 
                        choices=JOURNAL_STATUS, 
                        default="draft" # Workflow control: "draft" until validated, then "posted".
                        """ Once posted, becomes immutable. """
                        )
    posted_at = models.DateTimeField(null=True, blank=True)
    # Track user who created it
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    # optional polymorphic source info (invoice, bill, bank txn, fixed asset actions)
    source_type = models.CharField(max_length=50, null=True, blank=True) # Helps trace back where the JE originated
    source_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        # Speed up listing & filtering (e.g. show all posted entries this month)
        indexes = [models.Index(fields=["company", "date"]), models.Index(fields=["company", "status"])]

    def __str__(self):
        return f"JE {self.pk} {self.date} [{self.status}]"

    # Aggregate all debit and credit amounts across entry’s lines
    def compute_totals(self):
        """Return (debits, credits) sums for lines"""
        aggs = self.journalline_set.aggregate(
            total_debit=models.Sum("debit_amount"),
            total_credit=models.Sum("credit_amount"),
        )
        return (aggs["total_debit"] or Decimal("0.0"), aggs["total_credit"] or Decimal("0.0"))

    # True if double-entry rule holds: total debits = total credits
    def is_balanced(self):
        debit, credit = self.compute_totals()
        return (debit == credit)

    # Post the entry safely inside a database transaction
    def post(self, user=None):
        """Post the journal entry: check balance, mark posted, make lines immutable.
        Must run inside a transaction in views or service layer.
        """
        with transaction.atomic():
            # reload to lock
            # Lock the row to avoid race conditions
            je = JournalEntry.objects.select_for_update().get(pk=self.pk)
            if je.status == "posted":
                raise ValidationError("Already posted")
            d, c = je.compute_totals()
            # Enforce balance before marking posted
            if d != c:
                raise ValidationError("Journal entry not balanced: debit != credit")
            je.status = "posted" # Once posted → lines become immutable
            je.posted_at = timezone.now()
            if user:
                je.created_by = user
            je.save()
            # optionally: record ledgers / run balance snapshots etc.


class JournalLine(models.Model): # Stores Lines ( credits / debits )
    """
    Each line belongs to a journal entry and to a GL account.
    Optional foreign keys to invoice/bill/banktransaction/fixedasset for traceability.
    """
    # Belongs to company & a journal entry
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    journal = models.ForeignKey(JournalEntry, on_delete=models.CASCADE)

    # Must point to one Account (can’t delete account if lines exist → PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)

    # Description and the debit/credit split
    description = models.CharField(max_length=400, null=True, blank=True)
    debit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    credit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    
    # Link each posting line back to the business object that caused it
    invoice = models.ForeignKey("Invoice", null=True, blank=True, on_delete=models.SET_NULL)
    bill = models.ForeignKey("Bill", null=True, blank=True, on_delete=models.SET_NULL)
    bank_transaction = models.ForeignKey("BankTransaction", null=True, blank=True, on_delete=models.SET_NULL)
    fixed_asset = models.ForeignKey("FixedAsset", null=True, blank=True, on_delete=models.SET_NULL)

    # audit / immutability marker (populated when journal posted)
    is_posted = models.BooleanField(default=False) # prevents edits later

    class Meta:
        # For fast queries like “all lines for this account” / “all lines in this JE.”
        indexes = [
            models.Index(fields=["company", "account"]),
            models.Index(fields=["company", "journal"]),
        ]

        # Enforce debits and credits must be non-negative
        """ You can (optionally) add a CHECK constraint in Postgres to 
            prevent both debit & credit > 0 and at least one of them non-zero. 
            Django 3.2+ supports CheckConstraint. """
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(debit_amount__gte=0) & models.Q(credit_amount__gte=0)
                ),
                name="non_negative_amounts"
            ),
        ]

    # Business logic validation: 
    # each line must be either debit or credit, not both, not zero.
    def clean(self):
        # ensure debit xor credit or both allowed? Usually one is zero.
        if (self.debit_amount > 0) and (self.credit_amount > 0):
            raise ValidationError("JournalLine should not have both debit and credit > 0")
        if (self.debit_amount == 0) and (self.credit_amount == 0):
            raise ValidationError("JournalLine requires a non-zero amount on either debit or credit")