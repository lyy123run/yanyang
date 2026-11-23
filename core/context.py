def current_profile(request):
    if not request.user.is_authenticated:
        return {}
    return {"nav_profile": getattr(request.user, "profile", None)}
