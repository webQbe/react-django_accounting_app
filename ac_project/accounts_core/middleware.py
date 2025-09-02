from django.utils.deprecation import MiddlewareMixin
from .models import Company


class CurrentCompanyMiddleware(MiddlewareMixin):
    # Run on every request and 
    # can attach a .company attribute to the request, based on the logged-in user
    def process_request(self, request):
           if request.user.is_authenticated: # Check authentication
               # Assume user has default_company set (from EntityMembership)
               # Default company fallback: If user didn’t choose a company
               request.company = getattr(request.user, "default_company", None)

               # If user switched companies, 
               # choice is stored in the session as "active_company_id"
               company_id = request.session.get("active_company_id") # load company from database
               if company_id:
                try:
                    # ensure security: user must be a member of that company
                    company = Company.objects.get(id=company_id, memberships__user=request.user)
                except Company.DoesNotExist:
                    company = None 
                    # prevent someone from tampering with their session and
                    #  “jumping” into another company.
                request.company = company

           else: 
                # Unauthenticated users
                request.company = None
       