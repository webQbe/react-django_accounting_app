from django.db import models        # ORM base classes to define database tables as Python classes
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from django.conf import settings    # To access global project settings
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from django.db import models, transaction           # To wrap operations in a DB transaction
from django.utils import timezone                   # Timezone-aware datetime helper
from django.contrib.auth.models import AbstractUser

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
        """
            Model keeps pure business logic (totals, balance checks, state transition)
            - Ensure balanced debits/credits
            - Mark as posted
            Transaction management + orchestration moved to services.py
        """
        # Check for status
        if self.status == "posted":
                raise ValidationError("Already posted")
        # Enforce balance before marking posted
        if not self.is_balanced():
                raise ValidationError("Journal entry not balanced: debit != credit")
        self.status = "posted" # Once posted → lines become immutable
        self.posted_at = timezone.now()
        if user:
                self.created_by = user
        self.save()


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

    class Meta:
        # Optimize for fast lookups by invoice number or customer
        indexes = [ models.Index(fields=["company", "invoice_number"]), 
                    models.Index(fields=["company", "customer"])]

    def __str__(self):
        # If no invoice number, fall back to database ID
        return f"Inv {self.invoice_number or self.pk}"


class InvoiceLine(models.Model): # Each line describes a product/service sold on the invoice
    
    # Line belongs to both company and parent invoice
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)

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

    class Meta:
        # Speed up queries like “all lines for this invoice.”
        indexes = [models.Index(fields=["company", "invoice"])]

    def save(self, *args, **kwargs):
        # Automatically calculate line_total before saving
        self.line_total = (self.quantity or 0) * (self.unit_price or 0)
        super().save(*args, **kwargs)


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
    status = models.CharField(max_length=20, default="draft")  # draft, open, paid, void
    
    # Sum of all bill lines
    total = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    
    # How much is still unpaid
    outstanding_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        # Optimize queries for “lookup by bill number” or “all bills for this vendor.”
        indexes = [models.Index(fields=["company", "bill_number"]), 
                   models.Index(fields=["company", "vendor"])]

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

    class Meta:
        # For fast lookups of all lines on a given bill
        indexes = [models.Index(fields=["company", "bill"])]

    def save(self, *args, **kwargs):
        # Automatically calculate line_total on save
        self.line_total = (self.quantity or 0) * (self.unit_price or 0)
        super().save(*args, **kwargs)

# ---------- Banking ----------

class BankAccount(models.Model): # Represents bank account company maintains
    # Belongs to a Company (multi-tenant)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    name = models.CharField(max_length=200) # e.g. "Checking Account", "Savings Account"
    # Partial account number for display/security
    account_number_masked = models.CharField(max_length=50, null=True, blank=True)
    currency_code = models.CharField(max_length=10, default="USD")
    last_reconciled_at = models.DateField(null=True, blank=True) # For reconciliation workflows

    class Meta:
        # A company cannot have two accounts with the same name
        unique_together = ("company", "name")
        # Indexed for fast lookup
        indexes = [models.Index(fields=["company", "name"])]


class BankTransaction(models.Model): # Represents single inflow/outflow in a bank account
    # Belongs to both a Company and a specific BankAccount
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE)
    payment_date = models.DateField() # when it cleared
    # amount: positive = inflow (deposit), negative = outflow (payment)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency_code = models.CharField(max_length=10, default="USD")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="bank_transfer")
    reference = models.CharField(max_length=200, null=True, blank=True)
    

    class Meta:
        # Optimizes queries for reconciliation 
        # (find all txns for a bank account or for a date)
        indexes = [
                    models.Index(fields=["company", "bank_account"]), 
                    models.Index(fields=["company", "payment_date"])
                ]


class BankTransactionInvoice(models.Model): # Bridge table for applying bank transactions to invoices (AR settlements)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Many-to-many relationship between BankTransaction & Invoice
    bank_transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    # Allow partial application (e.g. $100 payment applied to a $250 invoice)
    applied_amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        indexes = [models.Index(fields=["company", "bank_transaction"]), 
                   models.Index(fields=["company", "invoice"])]
        
        # Each bank transaction can be linked to the same invoice only once
        constraints = [
            models.UniqueConstraint(
                                    fields=["bank_transaction", "invoice"], 
                                    name="unique_bank_tx_invoice"
                                )
        ]

 
class BankTransactionBill(models.Model): # Bridge table for applying bank transactions to bills (AP settlements)
    # Same idea as invoices, but for vendor payments
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    bank_transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE)
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE)
    # Supports partial payments
    applied_amount = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        # Prevent duplicate application of the same bank transaction to the same bill
        constraints = [
            models.UniqueConstraint(
                fields=["bank_transaction", "bill"], 
                name="unique_bank_tx_bill"
                )
        ]


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
    
    class Meta:
        # Index makes lookup faster by (company, asset_code) 
        # (since assets are often tracked by code)
        indexes = [models.Index(fields=["company", "asset_code"])]

    def __str__(self):
        # Return description when you print an asset in Django shell/admin
        return self.description

# ---------- Account Balance Snapshot (optional materialized) ----------
class AccountBalanceSnapshot(models.Model): # Summary / materialized snapshot used for reporting performance
   
    # Tied to a specific tenant (multi-company setup)
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    # Snapshot is for a specific GL account (like Cash, Accounts Payable, Sales)
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    # The date snapshot is taken (daily, monthly, or at reporting cutoffs (e.g., end of period))
    snapshot_date = models.DateField()

    # Hold account balance split into debit/credit buckets
    debit_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    credit_balance = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    """ Example:
            Cash account might show Debit = 10,000; Credit = 0.
            Accounts Payable might show Debit = 0; Credit = 5,000. """
    
    class Meta:
        # Ensure you don’t store duplicate snapshots for the same account/date
        unique_together = ("company", "account", "snapshot_date")
        # Optimize queries like: “Get all account balances for Company A on 2025-08-31.”
        indexes = [models.Index(fields=["company", "snapshot_date"])]


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

    class Meta:
        # one user can only have one membership per company (prevents duplicates)
        unique_together = ("user", "company")
        # make lookups fast (important since almost every query will filter by company)
        indexes = [
            models.Index(fields=["company", "user"]),
        ]

    def __str__(self):
        # Make debugging/admin easier
        return f"{self.user} @ {self.company} ({self.role})"