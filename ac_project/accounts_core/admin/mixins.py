class TenantAdminMixin:
    """
    Enforce tenant isolation in Django admin.
    Uses request.company (set by your CurrentCompanyMiddleware)
    or falls back to request.user.company.
    """

    def _get_request_company(self, request):
        # prefer request.company (middleware)
        # but fallback to request.user.company if present
        company = getattr(request, "company", None)
        if company is None:
            user = getattr(request, "user", None)
            if user and hasattr(user, "company"):
                company = getattr(user, "company")
        return company

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # if super returned None, return an empty queryset instead
        if qs is None:
            from django.apps import apps

            return apps.get_model(
                self.model._meta.app_label, self.model._meta.model_name
            ).objects.none()

        company = self._get_request_company(request)

        # If superuser, show everything;
        # otherwise restrict to company if available
        if request.user.is_superuser:
            return qs
        if company is None:
            # If no company available in request, return none
            return qs.none()
        return qs.filter(company=company)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Restrict foreignkey dropdowns to the current company where appropriate.
        Example:
        company field, account field,
        customer/vendor field that are company-scoped.
        """
        company = self._get_request_company(request)

        # If FK is to Company and user is not superuser,
        # restrict to user's company
        if db_field.name == "company" and not request.user.is_superuser:
            if company is not None:
                kwargs["queryset"] = db_field.related_model.objects.filter(
                    pk=company.pk
                )
            else:
                kwargs["queryset"] = db_field.related_model.objects.none()
            return super().formfield_for_foreignkey(
                db_field, request, **kwargs)

        # if related model has a `company` field,
        # restrict it to request's company
        rel_model = getattr(db_field, "related_model", None)
        if (
            rel_model is not None
            and hasattr(rel_model, "company")
            and not request.user.is_superuser
        ):
            if company is not None:
                kwargs["queryset"] = rel_model.objects.filter(company=company)
            else:
                kwargs["queryset"] = rel_model.objects.none()

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # Ensure object is always owned by company on save (unless superuser)
        if not request.user.is_superuser:
            company = self._get_request_company(request)
            if company is not None:
                obj.company = company
        super().save_model(request, obj, form, change)
