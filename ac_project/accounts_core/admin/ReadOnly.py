from django.contrib import admin
from django.core.exceptions import PermissionDenied

"""Base admin for read-only models with helpful list/search/filter defaults."""
class ReadOnlyAdmin(admin.ModelAdmin):
    list_per_page = 50  # page size (adjust for performance)
    ordering = None  # leave to model/view; optionally set to ('company_id', 'period_id')
    
    # make every model field readonly
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # Allow viewing the change form (read-only) by returning True here.
    # Prevent any saves by overriding save_model.
    def has_change_permission(self, request, obj=None):
        # Allow the user to view the instance page; 
        # actual edits are prevented because fields are readonly.
        return True
    
    # Prevent any attempt to save via the admin UI
    def save_model(self, request, obj, form, change):
        raise PermissionDenied("Rows cannot be changed via the admin.")
    
    # Disable admin actions like delete_selected
    def get_actions(self, request):
        return {}
    
    # Common useful filters if present
    def get_list_filter(self, request):
        possible = {f.name for f in self.model._meta.fields}
        filters = []
        for candidate in ("company_id", "period_id", "account_id", "account_type"):
            if candidate in possible:
                filters.append(candidate)
        return tuple(filters)
    
    # Useful searchable text fields if present
    def get_search_fields(self, request):
        possible = {f.name for f in self.model._meta.fields}
        search = []
        for candidate in ("account_code", "account_name"):
            if candidate in possible:
                search.append(candidate)
        return tuple(search)