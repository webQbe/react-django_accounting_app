from django.db import models        # ORM base classes to define database tables as Python classes
from .entitymembership import Company 
from ..managers import TenantManager
from django.core.exceptions import ValidationError  # Built-in way to raise validation errors


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
