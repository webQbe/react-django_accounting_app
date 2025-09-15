from django.db import models   # ORM base classes to define database tables as Python classes
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors
from decimal import Decimal         # Used for exact decimal arithmetic (money values, accounting entries)
from ..managers import TenantManager
from .entitymembership import Company
from .bill import Bill

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
            from .invoice import BankTransactionInvoice
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
