from django.db import \
    models  # ORM base classes to define database tables as Python classes

from ..managers import TenantManager
from .entitymembership import Company


# ---------- Chart of Accounts ----------
class AccountCategory(models.Model):  # For organizing accounts into categories
    # each company has its own set of categories (multi-tenant safe)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE
    )
    # category’s label (e.g. "Current Assets")
    name = models.CharField(max_length=100)
    # Enforce tenant scoping
    objects = TenantManager()

    class Meta:
        # Account category names repeat across companies
        # but must be unique within one
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                name="uq_company_accountcategory_name"
            ),
        ]

    def __str__(self):
        comSlug = self.company.slug
        name = self.name
        return f"{comSlug} - {name}"  # Example: "acme - Current Assets"
