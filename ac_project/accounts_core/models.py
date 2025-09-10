from django.db import models        # ORM base classes to define database tables as Python classes
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from django.conf import settings    # To access global project settings
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from django.db import models, transaction           # To wrap operations in a DB transaction
from django.utils import timezone                   # Timezone-aware datetime helper
from django.contrib.auth.models import AbstractUser
from .managers import TenantManager  # To enforce tenant scoping   

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

JOURNAL_STATUS = [
    ("draft", "Draft"),       # still editable
    ("posted", "Posted"),     # finalized
    ("reversed", "Reversed"), # reversal entry applied
]

PAYMENT_METHODS = [
    # Used in BankTransaction or Payment entities
    # Keeps payment method standardized across records
    ("cash", "Cash"),
    ("cheque", "Cheque"),
    ("bank_transfer", "Bank Transfer"),
    ("card", "Card"),
    ("other", "Other"),
]

BT_STATUS_CHOICES = [
        ("unapplied", "Unapplied"),
        ("partially_applied", "Partially applied"),
        ("fully_applied", "Fully applied"),
    ]

# ---------- Tenant / Company ----------
class Company(models.Model):
    """Tenant / Organization""" 
    name = models.CharField(max_length=200) # Store company’s full display name

    slug = models.SlugField( # A URL-friendly identifier
                            max_length=80, 
                            unique=True  # no two companies can have the same slug
                            ) 

    # Reference Currency model with ForeignKey
    default_currency = models.ForeignKey(
        "Currency",
        # don’t allow deleting a currency that a company depends on
        on_delete=models.PROTECT,   
        related_name="companies"
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

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # Account category names repeat across companies but must be unique within one
        constraints = [
          models.UniqueConstraint(fields=["company", "name"], 
                                  name="uq_company_accountcategory_name"),
      ]

    def __str__(self):
        return f"{self.company.slug} - {self.name}" # Example: "acme - Current Assets"

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
    

# ---------- Period (accounting period) ----------
class Period(models.Model): # Each Period represents a time bucket during which financial transactions are grouped
   
    # Every company has its own independent calendar of periods
    company = models.ForeignKey(Company, 
                                # Prevent accidental deletion of periods tied to journal entries, invoices, or bills
                                on_delete=models.PROTECT
                                )
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

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:

        # for filtering open periods
        indexes = [ 
                    models.Index(fields=["company", "start_date"]),
                    models.Index(fields=["company", "is_closed"]),
                ]

        # Prevent duplicate period names inside the same company
        constraints = [
          models.UniqueConstraint(fields=["company", "name"], 
                                  name="uq_company_period_name"),
      ]
        
        # Default query ordering: periods are returned sorted by company, then chronologically
        ordering = ("company", "start_date") # no need to sort manually

    def __str__(self):
        return f"{self.company.slug} {self.name}" # Example: "acme 2025-07".
    
    def clean(self):
        if self.start_date >= self.end_date:
            raise ValidationError("start_date must be before end_date")
        
    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)


# ---------- Customers & Vendors ----------
class Customer(models.Model): # Represents client who receives invoices (AR side)

    # Multi-tenant: every customer belongs to a single company.
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    """ Example: 
        Company A can have its own customers separate from Company B. 
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
                            # i.e., which customers use this AR account as default
                            related_name="customers_default_ar",
                            help_text="Default AR account used for this customer"
                        )

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:

        indexes = [ 
                    models.Index(fields=["company", "name"]),
                    models.Index(fields=["company", "default_ar_account"]),
                ]

        # Enforce uniqueness per tenant
        constraints = [
          models.UniqueConstraint(fields=["company", "name"], 
                                  name="uq_company_customer_name"),
        ]

    # Display customer name in admin/UI
    def __str__(self):
        return self.name

    
    def clean(self):
        # Ensure AR account belongs to the same company
        if self.default_ar_account and self.default_ar_account.company_id != self.company_id:
            raise ValidationError(
                "Default AR account must belong to the same company as the customer."
            )
        
        # Only control accounts can be set as default AR (Customer) 
        if self.default_ar_account and not self.default_ar_account.is_control_account:
            raise ValidationError("Default AR account must be a control account")
        
        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)


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
                                related_name="vendors_default_ap", # Lets you see which vendors use a given AP account
                                help_text="Default AP account used for this vendor"
                            )

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        indexes = [ 
                    models.Index(fields=["company", "name"]),
                    models.Index(fields=["company", "default_ap_account"]),
                ]

        # Vendor names must be unique per company
        constraints = [
          models.UniqueConstraint(fields=["company", "name"], 
                                  name="uq_company_vendor_name"),
        ]

    def __str__(self):
        return self.name

    
    def clean(self):
        # Ensure AR account belongs to the same company
        if self.default_ap_account and self.default_ap_account.company_id != self.company_id:
            raise ValidationError(
                "Default AP account must belong to the same company as the vendor."
            )

        # Only control accounts can be set as default AP (Vendor)
        if self.default_ap_account and not self.default_ap_account.is_control_account:
            raise ValidationError("Default AP account must be a control account") 
        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)

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
    
    # FK → Expense account for purchases/bills
    """ Example: "Printer Paper" → posts to "6000: Office Supplies Expense". """
    purchase_account = models.ForeignKey(  Account, 
                                            null=True, blank=True, 
                                            # if the linked account is deleted, the item stays but without a default account
                                            on_delete=models.SET_NULL, 
                                            related_name="items_purchase_account"
                                        )
    
    # Current stock level of the item
    on_hand_quantity = models.DecimalField(  
                                        max_digits=14, decimal_places=4, # Allow precise tracking - supports large quantities with fractional amounts, e.g. liters.
                                        default=Decimal("0.0")           # Default = 0
                                    )

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # for fast lookups
        indexes = [models.Index(fields=["company", "name"])] 

        # Ensure each SKU is unique within a company
        constraints = [
            models.UniqueConstraint(
                                    fields=["company", "sku"], 
                                    name="uq_company_item_sku"
                                )
        ]

    def __str__(self):
        return self.name
    
    """ Can’t create an Item for Company A but point it to an Account from Company B """
    def clean(self):
        if self.sales_account and self.sales_account.company_id != self.company_id:
            raise ValidationError("Sales account must belong to the same company as the item.")
        if self.purchase_account and self.purchase_account.company_id != self.company_id:
            raise ValidationError("Purchase account must belong to the same company as the item.")

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)

# ---------- Journal (Header) & JournalLine ----------
class JournalEntry(models.Model): # Represents one accounting transaction 
    # Header-level info
    # Multi-tenant: every entry belongs to a company
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Optional link to an accounting period (for reporting, closing)
    period = models.ForeignKey(
                                Period, null=True, blank=True, 
                                on_delete=models.PROTECT # Prevent breaking historical ledger
                            )
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

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # Speed up listing & filtering (e.g. show all posted entries this month)
        indexes = [models.Index(fields=["company", "date"]), models.Index(fields=["company", "status"])]

        constraints = [
            # Within one company, each journal entry must be unique
            # Across companies, duplicates are allowed
            models.UniqueConstraint(
                                    fields=["company", "reference"], 
                                    name="uq_je_company_ref"
                                )
        ]     
    def __str__(self):
        return f"JE {self.pk} {self.date} [{self.status}]"

    # Aggregate all debit and credit amounts across entry’s lines
    def compute_totals(self):
        """Return debits, credits sums for lines"""
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
        """
           Enforce accounting correctness (balanced, immutable, tenant-safe) and
           Update snapshots
        """
        # lazy import to avoid circular import at module load time
        from .services import update_snapshots_for_journal

        with transaction.atomic(): # Wrap whole posting process in a single DB transaction
            # Lock all journal lines to prevent race conditions
            # so no other transaction can modify them while posting is in progress
            lines = JournalLine.objects.select_for_update().filter(journal=self)

            """ Business validations """
            if not lines.exists(): # Prevent posting an empty entry
                raise ValidationError("JournalEntry must have at least one JournalLine.")

            # Recompute totals fresh from DB & ignore any stale cached values
            total_debit = Decimal('0.00')
            total_credit = Decimal('0.00')
            for l in lines:
                total_debit += l.debit_amount or Decimal('0.00')
                total_credit += l.credit_amount or Decimal('0.00')

            # Enforce double-entry rule: debits = credits
            if total_debit != total_credit:
                raise ValidationError("Journal does not balance: debits != credits")

            # Journals are immutable once posted. Prevent double posting
            if self.status.posted:
                raise ValidationError("Journal already posted")

            # Enforce tenant consistency
            # every line must belong to same company as journal
            if lines.exclude(company=self.company).exists():
                raise ValidationError("All journal lines must belong to same company as journal.")
            
            # Check period
            if self.period.exclude(company=self.company).exists():
                raise ValidationError("Period must belong to same company as journal")
            
            # Check creator
            if self.created_by.exclude(company=self.company).exists():
                raise ValidationError("Creator must belong to same company as journal")
            
            # Ensure periods open
            if self.period and self.period.is_closed:
                raise ValidationError("Period is closed")
            
            
            """ Update state """
            self.status.posted = True # Mark journal as posted
            self.posted_at = timezone.now() # Timestamp
            if user:
                self.created_by = user
            self.save(update_fields=["posted", "posted_at", "created_by"])

            # mark all lines as posted (bulk update)
            lines.update(is_posted=True)

        # Service layer function to trigger snapshot update
        # Recalculates AccountBalanceSnapshot for all accounts affected by this journal
        update_snapshots_for_journal(self) 

    def clean(self):
        """ Don't modify posted journals """
        if self.pk and self.status.posted: # If it has a primary key and status is posted, it's an update
            # Load original DB version before edits
            orig = JournalEntry.objects.get(pk=self.pk)
            # if attempting to change any core fields after posted
            if orig.status.posted:
                changed = False # allow no changes if posted (strict)
                # compare changes in "description", "period_id" fields
                for f in ("description", "period_id"):
                    if getattr(orig, f) != getattr(self, f):
                         # If they differ, then user is trying to change something after posting
                        changed = True 
                if changed:
                    # Block the save with a ValidationError
                    raise ValidationError("Cannot modify a posted JournalEntry. It is immutable.")

        """ Don't allow journals in closed periods """
        if self.period and self.period.is_closed:
            # If journal is assigned to a period, and the period is marked closed
            # Reject save
            raise ValidationError("Cannot create or edit journal inside a closed period.")

    def save(self, *args, **kwargs):
        if self.pk: # Does this row already exist in DB?
            # Fetch "original" row to update
            orig = JournalEntry.objects.get(pk=self.pk) 
            # Check if journal was already posted   
            if orig.status == "posted" and self.status != "posted":
                # disallow toggling posted flag
                raise ValidationError("Cannot unpost a posted journal")
                """ If self.status != "posted" → the user is trying to change status 
                    back to "draft" (or anything else). """  
            
        # If validation passes, continue with normal save
        super().save(*args, **kwargs)

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
    bank_transaction = models.ForeignKey(  "BankTransaction", 
                                            null=True, blank=True, 
                                            # on deleting a bank transaction
                                            on_delete=models.PROTECT # prevent breaking audit trails
                                         )
    fixed_asset = models.ForeignKey(
                                    "FixedAsset", null=True, blank=True,
                                    # on deleting a fixed_asset 
                                    on_delete=models.PROTECT # do not remove posted journal entries
                                 )

    # audit / immutability marker (populated when journal posted)
    is_posted = models.BooleanField(default=False) # prevents edits later

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # For fast queries like “all lines for this account” / “all lines in this JE.”
        indexes = [
            models.Index(fields=["company", "account"]),
            models.Index(fields=["company", "journal"]),
        ]

        # Enforce debits and credits must be non-negative
        """ You can optionally add a CHECK constraint in Postgres to 
            prevent both debit & credit > 0 and at least one of them non-zero. 
            Django 3.2+ supports CheckConstraint. """
        constraints = [
            models.CheckConstraint(
                check=(
                    # You can’t insert or update a row with a negative debit or credit
                    models.Q(debit_amount__gte=0) & 
                    models.Q(credit_amount__gte=0)
                    # Django reuses Q objects to build SQL conditions for constraints
                ),
                name="jl_non_negative_amounts"
            ),
        ]

    # Show journal, account, and amounts in admin dropdowns and debug logs 
    def __str__(self):
        return f"{self.journal_id} | {self.account.code} {self.account.name} | D:{self.debit_amount or 0} C:{self.credit_amount or 0}"
   
    # Business logic validation: 
    # - Debit/credit should always be non-negative
    # - Prevent mixing payable and receivable logic on one line
    # - Prevent “cross-company” contamination 
    def clean(self):
        # Ensures no negative values sneak in
        # (redundant with CheckConstraint but useful at app-level)
        if self.debit_amount < 0 or self.credit_amount < 0:
            raise ValidationError("Debit and credit must be >= 0")
        
        # ensure debit xor credit or both allowed? 
        # Usually one is zero.
        if (self.debit_amount > 0) and (self.credit_amount > 0):
            raise ValidationError("JournalLine should not have both debit and credit > 0")
        if (self.debit_amount == 0) and (self.credit_amount == 0):
            raise ValidationError("JournalLine requires a non-zero amount on either debit or credit")
       
        # Invoice and Bill cannot both be set
        # A journal line can link to either an invoice or a bill, but never both
        if self.invoice and self.bill:
            raise ValidationError("JournalLine cannot reference both invoice and bill.")
        
        # Company consistency
        # Every line must belong to same company as its parent journal
        if self.journal and self.company != self.journal.company:
            raise ValidationError("JournalLine.company must equal JournalEntry.company")
        # Ensure account chosen belongs to the same company
        if self.account and self.account.company != self.company:
            raise ValidationError("JournalLine.account must belong to the same company.")
        # Ensure invoice chosen belongs to the same company
        if self.invoice and self.invoice.company != self.company:
            raise ValidationError("JournalLine.invoice must belong to the same company.")
        # Ensure bill chosen belongs to the same company
        if self.bill and self.bill.company != self.company:
            raise ValidationError("JournalLine.bill must belong to the same company.")
        # Ensure bank transaction chosen belongs to the same company
        if self.bank_transaction and self.bank_transaction.company != self.company:
            raise ValidationError("JournalLine.bank_transaction must belong to the same company.")
        # Ensure fixed asset chosen belongs to the same company
        if self.fixed_asset and self.fixed_asset.company != self.company:
            raise ValidationError("JournalLine.fixed_asset must belong to the same company.")

        # Check if an Invoice/Bill/Asset references a non-control account → block it
        if self.invoice and not self.account.is_control_account:
            raise ValidationError("Invoice postings must use a control AR account.")
        if self.bill and not self.account.is_control_account:
            raise ValidationError("Bill postings must use a control AP account.")
        if self.fixed_asset and not self.account.is_control_account:
            raise ValidationError("Fixed asset postings must use a control account.")


    # save() override    
    def save(self, *args, **kwargs):
        # clean()+field validation always run whenever you save a JournalLine programmatically
        self.full_clean() 
        return super().save(*args, **kwargs)

# ---------- Invoices / InvoiceLines ----------
class Invoice(models.Model): # Represents a customer invoice
    
    # Invoice belongs to one company (multi-tenant)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    
    # Optionally linked to a Customer 
    # (if deleted, invoice keeps record but customer becomes NULL)
    customer = models.ForeignKey(
                                Customer, null=True, blank=True, 
                                # prevent deleting customer who has an invoice
                                on_delete=models.PROTECT
                        )
   
    # Identifiers and key dates
    # human-readable (e.g. "INV-2025-001")
    invoice_number = models.CharField(max_length=64, null=True, blank=True) 
    date = models.DateField() # issue date
    due_date = models.DateField(null=True, blank=True) # payment deadline (can be auto-calculated from customer’s payment terms)
   
    status = models.CharField(max_length=20, default="draft")  # draft, open, paid, void
    """ Workflow:
        draft = not yet finalized.
        open = issued but not paid.
        paid = fully settled.
        void = canceled. """
    
    # Supports multiple currencies
    currency_code = models.CharField(max_length=10, default="USD")
    # Sum of all line totals
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    # Unpaid amount after payments are applied
    outstanding_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    
    # Enforce tenant scoping
    objects = TenantManager()  

    class Meta:
        # Optimize for fast lookups by invoice number or customer
        indexes = [ models.Index(fields=["company", "invoice_number"]), 
                    models.Index(fields=["company", "customer"])]
        
        constraints = [
            # Within one company, each invoice number must be unique
            # Across companies, duplicates are allowed
            models.UniqueConstraint(
                                    fields=["company", "invoice_number"], 
                                    name="uq_invoice_company_number"
                                )
        ]     

    def __str__(self):
        # If no invoice number, fall back to database ID
        return f"Inv {self.invoice_number or self.pk}"

    """ Ensure invoice's stored totals are always in sync with its lines and payments """
    def recalc_totals(self): # Recompute invoice totals every time
        
        # safe to call only when invoice has a pk (or okay to return zeros)
        # guard if no pk: there are no lines yet
        if not getattr(self, "pk", None):
            self.total = Decimal("0.00")
            self.outstanding_amount = Decimal("0.00")
            return
        
        # Defined invoice FK with related_name="lines" on InvoiceLine model
        lines = self.lines.all() # So, reverse relation `lines` auto-created on Invoice model
        # Calculate sum of all InvoiceLine.line_totals
        total = sum((l.line_total for l in lines), Decimal('0.00'))
        self.total = total  # Set total
        
        # Sum of all applied payments
        paid = sum(bt.applied_amount for bt in BankTransactionInvoice.objects.filter(invoice=self))
       
        # Calculate outstanding_amount = total - sum(payments applied)
        # if payments overshoot for any reason, it caps at 0, not negative
        self.outstanding_amount = max(total - paid, Decimal('0.00'))

    """ Prevent “dirty totals” or “negative receivables” from persisting """
    def save(self, *args, **kwargs):
        """ If this is a new invoice (no pk yet), 
                persist it first so inlines can reference it safely """
        is_new = not bool(self.pk)
        if is_new:
            # Ensure customer chosen belongs to the same company
            if self.customer and self.customer.company != self.company:
                raise ValidationError("Customer must belong to the same company.")
            # Save parent first to get a PK. 
            super().save(*args, **kwargs)
            return

        """ Existing invoice 
                Recompute before saving """
        self.recalc_totals()
        # ensure outstanding_amount non-negative
        if self.outstanding_amount < 0:
            # important, otherwise credits/payments could accidentally 
            # overpay an invoice and mess up reporting
            raise ValidationError("Outstanding amount cannot be negative")
        # Then saves normally
        super().save(*args, **kwargs)

    """ Prevent deleting invoices that already have payments applied """
    def delete(self, *args, **kwargs):
        has_payments = BankTransactionInvoice.objects.filter(invoice=self).exists()
        if has_payments:
            raise ValidationError("Cannot delete an invoice with applied payments.")
            # Void or credit an invoice, instead of deleting it outright
        return super().delete(*args, **kwargs)

    def transition_to(self, new_status):
        # Current state vs. allowed next states
        allowed = {
            "draft": ["open"],
            "open": ["paid"],
            "paid": []          # "paid" → (no further transitions)
        }
        # Look up what states are allowed from current self.status
        if new_status not in allowed.get(self.status, []):
            # If requested new_status isn’t allowed → block it
            raise ValidationError(f"Cannot go from {self.status} to {new_status}")
        
        # If valid, update self.status and persist with .save()
        self.status = new_status
        self.save()


class InvoiceLine(models.Model): # Each line describes a product/service sold on the invoice
    
    # Line belongs to both company and parent invoice
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")

    # Optionally linked to a predefined Item
    # Or just free-text description if it’s a custom line
    item = models.ForeignKey(
                                Item, null=True, blank=True, 
                                # Prevent deleting item which has been invoiced
                                on_delete=models.PROTECT
                            )
    description = models.TextField(null=True, blank=True)

    # Core pricing logic: quantity × unit_price = line_total
    quantity = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    
    # Post to the correct revenue GL account
    account = models.ForeignKey(
                                    Account, null=True, blank=True, 
                                    # You can’t delete an account if lines still point to it
                                    on_delete=models.PROTECT,
                                    help_text="Sales / revenue account for this line"
                                )

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # Speed up queries like “all lines for this invoice.”
        indexes = [
                   models.Index(fields=["company", "invoice"]),
                   models.Index(fields=["company", "account"])
                ]

        # Ensure quantity & unit_price are never negative
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gte=0) & 
                      models.Q(unit_price__gte=0),
                name="invl_non_negative_amounts",
            ),
        ]

    # Show something human-readable in Django Admin
    def __str__(self):
        return f"Invoice: {self.invoice.invoice_number} - Item: {self.item} - Total: {self.line_total}"


    """ Ensure individual line amounts are valid """
    def clean(self):
        if self.quantity is not None and self.quantity < 0: # Quantity must be non-negative
            raise ValidationError("Quantity must be >= 0")
        if self.unit_price is not None and self.unit_price < 0: # Unit price must be non-negative
            raise ValidationError("Unit price must be >= 0")
        
        # compute expected total 
        expected = (self.quantity or Decimal('0')) * (self.unit_price or Decimal('0'))
        if self.line_total != expected: # If it doesn’t, it recalculates (self-healing)
            self.line_total = expected 

        # Tenant safety: 
        # Never dereference self.invoice directly unless invoice_id exists
        if getattr(self, "invoice_id", None) and getattr(self, "company_id", None):
            """ safely obtain parent company_id from DB  """
            inv_company_id = Invoice.objects.only("company_id").get(pk=self.invoice_id).company_id
            # Only raise a tenant-mismatch error if both sides are known
            if self.company_id != self.invoice.company_id:
                raise ValidationError("InvoiceLine.company must match Invoice.company")
            # Check for tenant-mismatch in item    
            if self.company_id != self.item.company_id:
                raise ValidationError("InvoiceLine.company must match Item.company")
            # Check for tenant-mismatch in account    
            if self.company_id != self.account.company_id:
                raise ValidationError("InvoiceLine.company must match Account.company")
        

    """ Ensure no inconsistent invoice line can ever be persisted """
    def save(self, *args, **kwargs):
        # copy company_id from DB if invoice_id is present
        if not getattr(self, "company_id", None) and getattr(self, "invoice_id", None):
            self.company_id = Invoice.objects.only("company_id").get(pk=self.invoice_id).company_id
        # compute line_total always
        self.line_total = (self.quantity or Decimal('0')) * (self.unit_price or Decimal('0')) 
        # Run validation, this will call clean()
        self.full_clean() 
        return super().save(*args, **kwargs)


# ---------- Bills / BillLines ----------

class Bill(models.Model): # Header represents vendor bill (Accounts Payable document)

    # Bill belongs to a company (multi-tenant)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    
    # Linked to a Vendor
    vendor = models.ForeignKey(
                                Vendor, null=True, blank=True, 
                                # prevent deleting customer who has a bill
                                on_delete=models.PROTECT
                            )
    # Vendor’s bill/invoice number (e.g. "INV-4567")
    bill_number = models.CharField(max_length=64, null=True, blank=True)
    date = models.DateField()                          # bill date
    due_date = models.DateField(null=True, blank=True) # when payment is expected
    
    # Track workflow
    status = models.CharField(max_length=20, default="draft")  # draft, posted, paid

    # Supports multiple currencies
    currency_code = models.CharField(max_length=10, default="USD")
    
    # Sum of all bill lines
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    
    # How much is still unpaid
    outstanding_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # Optimize queries for “lookup by bill number” or “all bills for this vendor.”
        indexes = [models.Index(fields=["company", "bill_number"]), 
                   models.Index(fields=["company", "vendor"])]
        
        constraints = [
            # Within one company, each bill number must be unique
            # Across companies, duplicates are allowed
            models.UniqueConstraint(
                                    fields=["company", "bill_number"], 
                                    name="uq_bill_company_number"
                                )
        ]

    def __str__(self):
        # If no bill number, fall back to database ID
        return f"Bill: {self.bill_number or self.pk}"

    """ Ensure bill's stored totals are always in sync with its lines and payments """
    def recalc_totals(self): # Recompute bill totals every time
        # Defined bill FK with related_name="lines" on BillLine model
        lines = self.lines.all() # So, reverse relation `lines` auto-created on Bill model
        # Calculate sum of all BillLine.line_totals
        total = sum((l.line_total for l in lines), Decimal('0.00'))
        self.total = total  # Set total
        
        # Sum of all applied payments
        paid = sum(bt.applied_amount for bt in BankTransactionBill.objects.filter(bill=self))
       
        # Calculate outstanding_amount = total - sum(payments applied)
        # if payments overshoot for any reason, it caps at 0, not negative
        self.outstanding_amount = max(total - paid, Decimal('0.00'))

    """ Prevent “dirty totals” or “negative receivables” from persisting """
    def save(self, *args, **kwargs):
        """ If this is a new bill (no pk yet), 
                persist it first so inlines can reference it safely """
        is_new = not bool(self.pk)
        if is_new:
            # Ensure vendor chosen belongs to the same company
            if self.vendor and self.vendor.company != self.company:
                raise ValidationError("Vendor must belong to the same company.")
            # Save parent first to get a PK. 
            super().save(*args, **kwargs)
            return

        # Recompute before saving
        self.recalc_totals()
        # ensure outstanding non-negative
        if self.outstanding_amount < 0:
            # important, otherwise credits/payments could accidentally 
            # overpay a bill and mess up reporting
            raise ValidationError("Outstanding amount cannot be negative")
        # Then save normally
        super().save(*args, **kwargs)

    """ Prevent deleting bills that already have payments applied """
    def delete(self, *args, **kwargs):
        has_payments = BankTransactionBill.objects.filter(bill=self).exists()
        if has_payments:
            raise ValidationError("Cannot delete a bill with applied payments.")
            # Void or credit an bill, instead of deleting it outright
        return super().delete(*args, **kwargs)

class BillLine(models.Model): # Detail line represents individual items/services on the bill
   
   # Belongs to both a company and its parent bill
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE)

    # Optionally linked to a predefined Item
    item = models.ForeignKey(
                                Item, null=True, blank=True, 
                                # Prevent deleting item which has been billed
                                on_delete=models.PROTECT
                            )
    
    # Describes purchased item/service
    description = models.TextField(null=True, blank=True)
    
    # Pricing fields: quantity × unit_price = line_total
    quantity = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    
    # Posts to the correct expense (or inventory/asset) account in the GL
    account = models.ForeignKey(
                                Account, null=True, blank=True, 
                                on_delete=models.PROTECT,
                                help_text="Expense/purchase account for this line"
                            )
    
    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # For fast lookups of all lines on a given bill
        indexes = [
                    models.Index(fields=["company", "bill"]),
                    models.Index(fields=["company", "account"])
                ]

        # Ensure quantity & unit_price are never negative
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gte=0) & 
                      models.Q(unit_price__gte=0),
                name="bl_non_negative_amounts",
            ),
        ]

    
    """ Ensure individual line amounts are valid """
    def clean(self):
        if self.quantity < 0: # Quantity must be non-negative
            raise ValidationError("Quantity must be >= 0")
        if self.unit_price < 0: # Unit price must be non-negative
            raise ValidationError("Unit price must be >= 0")
        
        # line_total must equal quantity * unit_price
        expected = (self.quantity or Decimal('0')) * (self.unit_price or Decimal('0'))
        if self.line_total != expected: # If it doesn’t, it recalculates (self-healing)
            self.line_total = expected 

        # Tenant safety check
        # Never dereference self.bill directly unless bill_id exists
        if getattr(self, "bill_id", None) and getattr(self, "company_id", None):
            """ safely obtain parent company_id from DB  """
            bill_company_id = Bill.objects.only("company_id").get(pk=self.bill_id).company_id
            # Only raise a tenant-mismatch error if both sides are known
            if self.company_id != self.bill.company_id:
                raise ValidationError("BillLine.company must match Bill.company")
            # Check for tenant-mismatch in item    
            if self.company_id != self.item.company_id:
                raise ValidationError("BillLine.company must match Item.company")
            # Check for tenant-mismatch in account    
            if self.company_id != self.account.company_id:
                raise ValidationError("BillLine.company must match Account.company")

    """ Ensure no inconsistent bill line can ever be persisted """
    def save(self, *args, **kwargs):
        # copy company_id from DB if bill_id is present
        if not getattr(self, "company_id", None) and getattr(self, "bill_id", None):
            self.company_id = Bill.objects.only("company_id").get(pk=self.bill_id).company_id
        # Force line_total to be recomputed before save, regardless of input
        self.line_total = (self.quantity or 0) * (self.unit_price or 0)
        self.full_clean() # Run all validations in clean() again
        return super().save(*args, **kwargs) # Then finally save

# ---------- Banking ----------

class BankAccount(models.Model): # Represents bank account company maintains
    # Belongs to a Company (multi-tenant)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    name = models.CharField(max_length=200) # e.g. "Checking Account", "Savings Account"
    # Partial account number for display/security
    account_number_masked = models.CharField(max_length=50, null=True, blank=True)
    currency_code = models.CharField(max_length=10, default="USD")
    last_reconciled_at = models.DateField(null=True, blank=True) # For reconciliation workflows

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # A company cannot have two accounts with the same name
        constraints = [
          models.UniqueConstraint(fields=["company", "name"], 
                                  name="uq_company_bankaccount_name"),
        ]
        # Indexed for fast lookup
        indexes = [models.Index(fields=["company", "name"])]

    def __str__(self):
        # In your BankTransaction form (admin)
        # Show name + masked number for clarity
        if self.account_number_masked:
            return f"{self.name} ({self.account_number_masked})"
        return self.name


class BankTransaction(models.Model): # Represents single inflow/outflow in a bank account
    # Belongs to both a Company and a specific BankAccount
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # BankAccount → on_delete=PROTECT → prevents BankAccount deletion if transactions exist
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    payment_date = models.DateField() # when it cleared
    # amount: positive = inflow (deposit), negative = outflow (payment)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency_code = models.CharField(max_length=10, default="USD")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="bank_transfer")
    status = models.CharField(max_length=20, choices=BT_STATUS_CHOICES, default="unapplied") # other statuses: partially_applied, fully_applied
    reference = models.CharField(max_length=200, null=True, blank=True)
    
    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # Optimizes queries for reconciliation 
        # (find all txns for a bank account or for a date)
        indexes = [
                    models.Index(fields=["company", "bank_account"]), 
                    models.Index(fields=["company", "payment_date"])
                ]

        constraints = [
            # Within one company, each bank transaction must be unique
            # Across companies, duplicates are allowed
            models.UniqueConstraint(
                                    fields=["company", "reference"], 
                                    name="uq_bt_company_ref"
                                )
        ]    

    def clean(self): # auto-runs when you call full_clean() before saving
        # Tenancy check
        # Ensure bank account chosen belongs to the same company
        if self.bank_account and self.bank_account.company != self.company:
            raise ValidationError("Bank account must belong to the same company.")
        
        # Currency check
        # Prevent mixing currencies in the same account ledger
        if self.currency_code != self.bank_account.currency_code:
            raise ValidationError("Transaction currency must match bank account currency")
        
        # Only check related rows if this transaction already saved/exists in DB
        if self.pk:
            # Applied amount check - ensure sum of applied <= amount
            # Find all join rows where this bank transaction was applied against invoices
            applied = BankTransactionInvoice.objects.filter(bank_transaction=self).aggregate(
                total=models.Sum("applied_amount")
            )["total"] or Decimal('0')

            # Prevent over-allocation: you can’t apply more than actual bank transaction’s amount
            if applied > self.amount:
                raise ValidationError("Applied payments exceed bank transaction amount")
            
    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
    
    # Show something human-readable in Django Admin
    def __str__(self):
        return f"{self.bank_account.name} - {self.payment_date} - {self.amount} {self.currency_code} ({self.status})"

    """How much of this transaction has been applied to invoices?"""
    def applied_total(self):
        return (
            self.banktransactioninvoice_set.aggregate(
                total=models.Sum("applied_amount")
            )["total"] or Decimal("0.00")
        )


    def transition_to(self, new_status):
        # Current state vs. allowed next states
        allowed = {
            "unapplied": ["partially_applied", "fully_applied"],
            "partially_applied": ["fully_applied"],
            "fully_applied": []       # "fully_applied" → (no further transitions)
        }
        # Look up what states are allowed from current self.status
        if new_status not in allowed.get(self.status, []):
            # If requested new_status isn’t allowed → block it
            raise ValidationError(f"Cannot go from {self.status} to {new_status}")
        
        applied = self.applied_total()

        if new_status == "partially_applied":
            if applied <= 0 or applied >= self.amount:
                raise ValidationError(
                    f"Cannot mark as partially_applied: applied={applied}, amount={self.amount}"
                )
            
        if new_status == "fully_applied":
            if applied != self.amount:
                raise ValidationError(
                    f"Cannot mark as fully_applied: applied={applied}, amount={self.amount}"
                )
        
        # If valid, update self.status and persist with .save()
        self.status = new_status
        self.save(update_fields=["status"])
        return self
        

class BankTransactionInvoice(models.Model): # Bridge table for applying bank transactions to invoices (AR settlements)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Many-to-many relationship between BankTransaction & Invoice
    bank_transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    # Allow partial application (e.g. $100 payment applied to a $250 invoice)
    applied_amount = models.DecimalField(max_digits=18, decimal_places=2)

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        indexes = [models.Index(fields=["company", "bank_transaction"]), 
                   models.Index(fields=["company", "invoice"])]
        
        
        constraints = [
            # Each bank transaction can be linked to the same invoice only once
            models.UniqueConstraint(
                                    fields=["bank_transaction", "invoice"], 
                                    name="unique_bank_tx_invoice"
                                ),
            # Ensure applied_amount is never negative                    
            models.CheckConstraint(
                                    check=models.Q(applied_amount__gte=0),
                                    name="bt_inv_non_negative_amounts",
                                ),
            ]

    # Show bank_transaction, invoice_number, and applied_amount in admin dropdowns and debug logs 
    def __str__(self):
        return f"BT: {self.bank_transaction} → Inv: {self.invoice.invoice_number} Amt: ({self.applied_amount})"
        
    def clean(self):
        # You can’t apply negative payment
        if self.applied_amount < 0: 
            raise ValidationError("Applied must be non-negative")
        
        # cannot apply more than outstanding
        # prevent “overpayment” situations where invoice would go negative
        if self.applied_amount > self.invoice.outstanding_amount:
            raise ValidationError("Applied amount cannot exceed invoice outstanding")
        
        # Prevent cross-company contamination
        # Ensure Bank transaction chosen belongs to the same company
        if self.bank_transaction and self.bank_transaction.company != self.company:
            raise ValidationError("Bank transaction must belong to the same company.")
        
        # Ensure invoice chosen belongs to the same company
        if self.invoice and self.invoice.company != self.company:
            raise ValidationError("Invoice must belong to the same company.")
        
        """ You can't accidentally link a BankTransaction 
            from Company A to an Invoice from Company B. """
        if self.invoice.company != self.bank_transaction.company:
            raise ValidationError("Invoice and BankTransaction must belong to same company")

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)
 
class BankTransactionBill(models.Model): # Bridge table for applying bank transactions to bills (AP settlements)
    # Same idea as invoices, but for vendor payments
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    bank_transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE)
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE)
    # Supports partial payments
    applied_amount = models.DecimalField(max_digits=18, decimal_places=2)

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        indexes = [models.Index(fields=["company", "bank_transaction"]), 
                   models.Index(fields=["company", "bill"])]
        
        constraints = [
            # Prevent duplicate application of the same bank transaction to the same bill
            models.UniqueConstraint(
                fields=["bank_transaction", "bill"], 
                name="unique_bank_tx_bill"
                ),
            # Ensure applied_amount is never negative                    
            models.CheckConstraint(
                                    check=models.Q(applied_amount__gte=0),
                                    name="bt_bill_non_negative_amounts",
                                ),
        ]

    # Show bank_transaction, bill_number, and applied_amount in admin dropdowns and debug logs 
    def __str__(self):
        return f"BT: {self.bank_transaction} → Bill: {self.bill.bill_number} Amt: ({self.applied_amount})"
    
    def clean(self):
        # You can’t apply negative payment
        if self.applied_amount < 0: 
            raise ValidationError("Applied must be non-negative")
        
        # cannot apply more than outstanding
        # prevent “overpayment” situations where bill would go negative
        if self.applied_amount > self.bill.outstanding_amount:
            raise ValidationError("Applied amount cannot exceed bill outstanding")
        
        # Prevent cross-company contamination
        # Ensure Bank transaction chosen belongs to the same company
        if self.bank_transaction and self.bank_transaction.company != self.company:
            raise ValidationError("Bank transaction must belong to the same company.")
        # Ensure bill chosen belongs to the same company
        if self.bill and self.bill.company != self.company:
            raise ValidationError("Bill must belong to the same company.")

        """ You can't accidentally link a BankTransaction 
            from Company A to an Bill from Company B. """
        if self.bill.company != self.bank_transaction.company:
            raise ValidationError("Bill and BankTransaction must belong to same company")

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)

# ---------- Fixed Assets ----------
class FixedAsset(models.Model): # tracks long-term assets and handle depreciation over time
    # Each fixed asset belongs to a company (multi-tenant)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # An optional identifier for the asset
    asset_code = models.CharField(max_length=80, null=True, blank=True)
    # Human-readable name/description
    description = models.CharField(max_length=400) 
    # Date the asset was bought
    purchase_date = models.DateField(null=True, blank=True) 
    # Acquisition cost - stored as Decimal for precision in accounting
    purchase_cost = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    # GL account that represents the asset 
    account = models.ForeignKey( 
        Account, 
        on_delete=models.PROTECT,
        null=True, blank=True, 
        limit_choices_to={"ac_type": "Asset"},
        help_text="GL account where this asset is capitalized",
    )
    # who sold it to you
    vendor = models.ForeignKey(
        Vendor, 
        null=True, blank=True,
        on_delete=models.PROTECT,
        help_text="Vendor from whom this asset was purchased",
    )
    # lifecycle state 
    status = models.CharField(
        max_length=20,
        choices=[
            ("draft", "Draft"),
            ("capitalized", "Capitalized"),
            ("disposed", "Disposed"),
        ],
        default="draft",
    )
    # Estimated lifespan in years (for depreciation)
    useful_life_years = models.IntegerField(null=True, blank=True) 
    depreciation_method = models.CharField(
                            max_length=30, 
                            default="straight_line"
                                """ How depreciation is calculated:
                                    "straight_line" = equal expense every year.
                                    Other methods could include "declining_balance", "units_of_production". """
                            )
    
    # Track how much depreciation has already been recorded
    accumulated_depreciation = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    """ Example: if a $12,000 asset is depreciated $4,000 per year, after 2 years this field = $8,000. """
    
    # Enforce tenant scoping
    objects = TenantManager() 
    
    class Meta:
        # Index makes lookup faster by company, asset_code
        # (since assets are often tracked by code)
        indexes = [models.Index(fields=["company", "asset_code"])]

        constraints = [
            # Within one company, each fixed asset must be unique
            # Across companies, duplicates are allowed
            models.UniqueConstraint(
                                    fields=["company", "asset_code"], 
                                    name="uq_fa_company_asset_code"
                                )
        ] 

    def __str__(self):
        # Return description when you print an asset in Django shell/admin
        return self.description
    
    def clean(self):
        # Tenancy checks
        # Ensure account chosen belongs to the same company
        if self.account and self.account.company != self.company:
            raise ValidationError("Account must belong to the same company.")
        # Ensure vendor chosen belongs to the same company
        if self.vendor and self.vendor.company != self.company:
            raise ValidationError("Vendor must belong to the same company.")

        # Prevent assets from being marked as depreciable when their lifespan hasn’t been set
        # Without a positive number of years, depreciation makes no sense
        if self.depreciation_method and (not self.useful_life_years or self.useful_life_years <= 0):
            raise ValidationError("Useful life must be > 0 if depreciation method is set")
        
        # Purchase cost cannot be negative
        if self.purchase_cost < 0:
            raise ValidationError("Purchase cost must be >= 0")
        
    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)

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
                check=(
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
    
# ---------- Audit / Event log ----------
class AuditLog(models.Model): # Gives accountability and traceability across whole system
    # Associate log entry with a tenant (multi-company setup)
    company = models.ForeignKey(
                                Company, 
                                # Nullable because some actions might not belong to a specific company (e.g., system-wide events).
                                null=True, blank=True, 
                                on_delete=models.SET_NULL
                            )
    # Which user performed the action 
    # (Nullable in case the action was automated (e.g., background job, import script))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    # Type of event being logged
    action = models.CharField(max_length=50) # Common choices: create, update, delete, post
    # What kind of object was affected 
    object_type = models.CharField(max_length=100) # (e.g., "Invoice", "JournalEntry", "Customer")
    # The primary key (or identifier) of the object
    object_id = models.CharField(max_length=100)
    # Store actual before/after details of what changed, in JSON format
    changes = models.JSONField(null=True, blank=True)
    # Timestamp when the event was logged
    created_at = models.DateTimeField(auto_now_add=True) 

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # Filter logs quickly
        indexes = [
                    models.Index(fields=["company", "user"]),
                    models.Index(fields=["company", "created_at"]),
                ]

    # Show created_at, user, action, object_type, and
    # object_id in admin dropdowns and debug logs 
    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.user} {self.action} {self.object_type}({self.object_id})"

    def clean(self):
        # Ensure the user is a member of the company being logged
        if self.user and self.company:
            if not self.user.memberships.filter(company=self.company, is_active=True).exists():
                raise ValidationError("AuditLog.user must be a member of AuditLog.company")

    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)

# ---------- Currency ----------
class Currency(models.Model): # Store a list of valid currencies
    """
    ISO currencies. Use currency.code FK in other tables instead of free-text.
    """
    # Set code as the primary key, so it uniquely identifies a currency
    code = models.CharField(max_length=3, primary_key=True)  # 'USD', 'EUR'
    # Human-readable name of the currency
    name = models.CharField(max_length=64)  # 'US Dollar'
    # Nullable display symbol ("$", "€", "¥")
    symbol = models.CharField(max_length=8, blank=True, null=True)  # '$'
    # Avoid mistakes like storing 12.345 for JPY (which has no sub-units)
    decimal_places = models.PositiveSmallIntegerField(default=2)

    def __str__(self):
        # Define how this model prints in Django admin
        return f"{self.code} ({self.symbol or ''})"

    class Meta:
        # Make admin display plural as “currencies” instead of default “currencys”
        verbose_name_plural = "currencies"


# ---------- Custom User (optional) ---------- 
class User(AbstractUser): # Replace built-in user with custom user to add add extra fields
    # Inherits from Django’s AbstractUser, so it keeps all the usual fields
    """ 
    Before you run your very first migrate,  
    add: 'AUTH_USER_MODEL = "accounts_core.User"' to settings.py
    to avoid migration conflicts
    """
    # A link to a Company (your tenant)
    default_company = models.ForeignKey("Company", 
                                        # Nullable, user might exist before being assigned company
                                        null=True, blank=True,
                                        # If the company is deleted, don’t delete the user, just clear their default company
                                        on_delete=models.SET_NULL,
                                        # From Company side, see which users have the company as default
                                        related_name="default_users")
    
    # Optional contact number field, can be left empty in forms
    phone = models.CharField(max_length=32, blank=True)

    # Enforce tenant scoping
    objects = TenantManager() 

    """     
        User.objects.create_user → makes a normal user.
        User.objects.create_superuser → makes a superuser (used by python manage.py createsuperuser)
    """

    class Meta:
        # helpful in multi-tenant setups
        indexes = [models.Index(fields=["default_company"])]

    # Controls how user is displayed
    def __str__(self):
        # Try to return full name
        return self.get_full_name() or self.username # Fall back to username if no name is set


# ---------- EntityMembership ----------
class EntityMembership(models.Model): # Bridge table (or a "join model") between User and Company
   
    # Limit roles to predefined values
    ROLE_CHOICES = [
        # Django admin / forms will show a dropdown with these choices
        ("owner", "Owner"),           # full control (e.g., the person who created the company)
        ("admin", "Admin"),           # can manage settings & users
        ("accountant", "Accountant"), # can post journals, invoices, but maybe not delete companies
        ("viewer", "Viewer"),         # read-only access
    ]

    # Link to custom User
    user = models.ForeignKey(  
                                settings.AUTH_USER_MODEL, 
                                on_delete=models.CASCADE,  # If user is deleted, their memberships go too
                                related_name="memberships" # See all companies users belong to
                            )
    
    # Links to a Company record
    company = models.ForeignKey("Company", on_delete=models.CASCADE, related_name="memberships")
    
    # Store user’s role in the company
    role = models.CharField(
                            max_length=20, choices=ROLE_CHOICES, 
                            default="viewer" # Defaults to "viewer" (safe, read-only)
                            )
    
    # Suspend someone’s access without deleting the record
    is_active = models.BooleanField(default=True)
    """ 
        Think of it like a switch that controls whether a membership is “turned on” without throwing it away.
            If is_active=True → the membership is valid. 
                The user has access to that company with their role.
            If is_active=False → the membership still exists in the database, 
                but you can treat it as “suspended” or “revoked.” 
    """

    # Automatically record when membership was created
    created_at = models.DateTimeField(auto_now_add=True)

    # Enforce tenant scoping
    objects = TenantManager() 

    class Meta:
        # one user can only have one membership per company (prevents duplicates)
        constraints = [
          models.UniqueConstraint(fields=["user", "company"], 
                                  name="uq_user_company_membership"),
        ]

        # make lookups fast (important since almost every query will filter by company)
        indexes = [
            models.Index(fields=["company", "user"]),
        ]

    def __str__(self):
        # Make debugging/admin easier
        return f"{self.user} @ {self.company} ({self.role})"

    def clean(self):
        if self.user and self.user.default_company:
            # Get user's memberships using `user` FK (related name = memberships)
            valid_company_ids = self.user.memberships.values_list("company", flat=True)
            # Check default company is among user’s memberships
            if self.user.default_company not in valid_company_ids:
                raise ValidationError(
                    f"Default company {self.user.default_company} must be one of user's memberships."
                )
            
    def save(self, *args, **kwargs):
        self.full_clean()  # run validations before saving
        return super().save(*args, **kwargs)