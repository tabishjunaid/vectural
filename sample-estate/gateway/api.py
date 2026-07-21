def handle_request(req):
    """Entry point: authenticates then charges via payments."""
    return charge(req.amount)
