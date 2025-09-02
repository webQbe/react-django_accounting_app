from django.db import models
# -----------------------------------------
# Enforce tenant scoping across all models 
# that belong to a company 
# -----------------------------------------
# Define subclass of Django’s QuerySet
class TenantQuerySet(models.QuerySet):      
    def for_company(self, company):         # Add helper
        return self.filter(company=company) # Apply filter
    # Enables query: 
    # Invoice.objects.for_company(request.user.default_company)

# Attach TenantQuerySet to .objects
class TenantManager(models.Manager): 
    def get_queryset(self): # ensure every model gets TenantQuerySet(so .for_company() is always available)
        return TenantQuerySet(self.model, using=self._db)

    def for_company(self, company): # can call for_company() directly on objects
        return self.get_queryset().for_company(company)
    
    # every model using TenantManager can call:
    # Invoice.objects.for_company(request.user.default_company)