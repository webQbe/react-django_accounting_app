from django.db import models        # ORM base classes to define database tables as Python classes
from .entitymembership import Company  
from ..managers import TenantManager

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