from django.shortcuts import render
from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from accounts_core.services import open_invoice, pay_invoice, apply_and_update_status
from .models import Invoice
from django.core.exceptions import ValidationError
from decimal import Decimal

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

def apply_payment_view(request, bt_id, invoice_id):
    amount = Decimal(request.POST.get("amount"))
    # call the service and handle response or errors
    try: 
        bt, inv = apply_and_update_status(bt_id, invoice_id, amount)
        return JsonResponse({"ok": True, "bt_status": bt.status, "invoice_status": inv.status})
    except ValidationError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)