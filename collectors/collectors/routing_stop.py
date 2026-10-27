"""Preserve the first shared receiver shutdown cause through queue draining."""
def request_stop(event,reason):
    if not event.is_set():event.reason=reason
    event.set()

def stop_reason(event,fallback='GracefulStop'):
    return getattr(event,'reason',fallback) if event.is_set() else fallback
