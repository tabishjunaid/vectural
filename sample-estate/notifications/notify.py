def worker():
    """Consume payment events and notify the customer."""
    subscribe("payments.events", on_event)


def on_event(evt):
    return evt
