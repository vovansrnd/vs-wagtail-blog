# blog/utils.py

def get_domain_theme(request):
    if not request:
        return "nova"
    host = request.get_host().lower()
    if "floppygames" in host or "floppy" in host:
        return "floppy"
    elif "zenway" in host:
        return "zenway"
    return "nova"
