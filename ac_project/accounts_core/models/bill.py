from django.db import models   # ORM base classes to define database tables as Python classes
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from ..managers import TenantManager
from .entitymembership import Company
from .account import Account
from .vendor import Vendor
from .item import Item

BILL_STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('posted', 'Posted'),
        ('paid', 'Paid'),
    ]

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
    status = models.CharField(max_length=20, choices=BILL_STATUS_CHOICES, default="draft")  # draft, posted, paid

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

        from .banking import BankTransactionBill
        # Sum of all applied payments
        paid = sum(bt.applied_amount for bt in BankTransactionBill.objects.filter(bill=self))
       
        # Calculate outstanding_amount = total - sum(payments applied)
        # if payments overshoot for any reason, it caps at 0, not negative
        self.outstanding_amount = max(total - paid, Decimal('0.00'))

    def clean(self):
        """ Make paid bills immutable in all code paths (admin, DRF API, custom services) """
        # If object already exists and is paid, prevent edits
        if self.pk and self.status == 'paid':
            orig = Bill.objects.get(pk=self.pk)
            changed_fields = []
            for field in ['bill_number', 'total', 'company']:
                # Check for edits
                if getattr(orig, field) != getattr(self, field):
                    changed_fields.append(field)
            if changed_fields:
                raise ValidationError(
                    f"Cannot modify {changed_fields} on a paid bill."
                )

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

        """ For existing bills """
        # Recompute before saving
        self.recalc_totals()
        # ensure outstanding non-negative
        if self.outstanding_amount < 0:
            # important, otherwise credits/payments could accidentally 
            # overpay a bill and mess up reporting
            raise ValidationError("Outstanding amount cannot be negative")
        self.full_clean()  # will trigger clean()
        # Then save normally
        super().save(*args, **kwargs)

    """ Prevent deleting bills that already have payments applied """
    def delete(self, *args, **kwargs):
        from .banking import BankTransactionBill
        has_payments = BankTransactionBill.objects.filter(bill=self).exists()
        if has_payments:
            raise ValidationError("Cannot delete a bill with applied payments.")
            # Void or credit an bill, instead of deleting it outright
        return super().delete(*args, **kwargs)
    
    def transition_to(self, new_status):
        # Current state vs. allowed next states
        allowed = {
            "draft": ["posted"],
            "posted": ["paid"],
            "paid": []          # "paid" → (no further transitions)
        }
        # Look up what states are allowed from current self.status
        if new_status not in allowed.get(self.status, []):
            # If requested new_status isn’t allowed → block it
            raise ValidationError(f"Cannot go from {self.status} to {new_status}")
        
        # If valid, update self.status and persist with .save()
        self.status = new_status
        self.save()

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
