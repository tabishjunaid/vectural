def charge(amount):
    """Charge a customer, apply it to the ledger, and emit an event."""
    applyCharge(amount)
    publish("payments.events", amount)
    return True
