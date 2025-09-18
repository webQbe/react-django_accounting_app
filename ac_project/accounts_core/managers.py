from django.db import models
from django.contrib.auth.base_user import BaseUserManager

# -----------------------------------------
# Enforce tenant scoping across all models 
# that belong to a company 
# -----------------------------------------
# Define subclass of Django’s QuerySet
class TenantQuerySet(models.QuerySet):  
    def for_company(self, company):         # Add queryset helper
        return self.filter(company=company) # Apply filter

    def active(self, company):      
        return self.filter( 
                            company=company, # enforce tenant scoping
                            is_active=True   # only fetch active records
                        ) 
    # Enables query: 
    # Invoice.objects.active(request.company)

# Attach TenantQuerySet to .objects
class TenantManager(BaseUserManager): # Inherits from BaseUserManager (so you don’t have to reinvent everything)
   
    def get_queryset(self): # ensure every model gets TenantQuerySet(so .for_company() is always available)
        return TenantQuerySet(self.model, using=self._db)

    def for_company(self, company): # can call for_company() directly on objects
        return self.get_queryset().for_company(company)
    
    def active(self, company):
        return self.get_queryset().active(company)
    
    # every model using TenantManager can call:
    # Invoice.objects.active(request.company)

    """ Enforce rules around how users are created """

    use_in_migrations = True # Allow Django to serialize this manager in migrations

    # Shared logic for both create_user() & create_superuser()
    # Private helper method used by both `create_user` and `create_superuser`
    def _create_user(self, username, email, password, **extra_fields):
        if not username: # Username is required
            raise ValueError("The given username must be set")
        email = self.normalize_email(email) # Email is normalized (lowercased domain part)
        user = self.model(username=username, email=email, **extra_fields)
        user.set_password(password) # Password is hashed 
        user.save(using=self._db) # User is saved with correct DB alias
        return user

    # Safe defaults for regular accounts
    # Used when you call User.objects.create_user(...)
    def create_user(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)  # Default
        extra_fields.setdefault("is_superuser", False) # Default
        return self._create_user(username, email, password, **extra_fields)
    
    # Strict enforcement that superusers must always have full privileges
    # Used by Django when running `createsuperuser`
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True) # Default
        extra_fields.setdefault("is_superuser", True) # Default
        # You cannot pass conflicting values
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True")
        return self._create_user(username, email, password, **extra_fields)
    
# Add company's default currency to JournalLine
class JournalLineCurrencyManager(models.Manager):
    def create_for_entry(self, journal_entry, **kwargs): # pass company linked journal_entry
        if "currency" not in kwargs: # if no currency is provided 
            # fill in company’s default currency
            kwargs["currency"] = journal_entry.company.default_currency
        # call normal create()
        return super().create(journal_entry=journal_entry, **kwargs)
    

# Create an InvoiceLine, defaulting unit_price from Item if not given.
class InvoiceLineUnitPriceManager(models.Manager):
    def create_from_item(self, item, **kwargs):
        if "unit_price" not in kwargs:
            kwargs["unit_price"] = getattr(item, "default_unit_price", None)
        kwargs["item"] = item
        return super().create(**kwargs)