from django.shortcuts import render
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from accounts_core.services import open_invoice, pay_invoice
from .models import Invoice

# Create your views here.
def open_invoice_view(request, invoice_id):
    # Look up Invoice by its primary key (invoice_id)
    invoice = get_object_or_404(Invoice, pk=invoice_id) # If no invoice found, raise 404 error (instead of crashing)
    open_invoice(invoice) # ensure invoice has lines before moving from status draft → open
    # After applying the state transition, return a JSON response to client
    return JsonResponse({"status": invoice.status})


def pay_invoice_view(request, invoice_id):
    invoice = get_object_or_404(Invoice, pk=invoice_id)
    # ensure invoice’s outstanding balance is 0 before moving from status open → paid
    pay_invoice(invoice)
    return JsonResponse({"status": invoice.status})