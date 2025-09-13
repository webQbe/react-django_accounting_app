from django.db import models   # ORM base classes to define database tables as Python classes
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from ..managers import TenantManager
from .entitymembership import Company
from .account import Account
from .customer import Customer
from .banking import BankTransactionInvoice
from .item import Item

INV_STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('open', 'Open'),
        ('paid', 'Paid'),
    ]



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
   
    status = models.CharField(max_length=10, choices=INV_STATUS_CHOICES, default="draft")  # draft, open, paid, void
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

    def clean(self):
        """ Make paid invoices immutable in all code paths (admin, DRF API, custom services) """
        # If object already exists and is paid, prevent edits
        if self.pk and self.status == 'paid':
            orig = Invoice.objects.get(pk=self.pk)
            changed_fields = []
            for field in ['invoice_number', 'total', 'company']:
                # Check for edits
                if getattr(orig, field) != getattr(self, field):
                    changed_fields.append(field)
            if changed_fields:
                raise ValidationError(
                    f"Cannot modify {changed_fields} on a paid invoice."
                )

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
        self.full_clean()  # will trigger clean()
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
