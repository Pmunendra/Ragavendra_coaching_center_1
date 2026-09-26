"""Very small dependency-free UA sniffing, good enough for logging purposes.
For heavier accuracy you can swap this for the `user-agents` PyPI package.
"""
import re


def parse_user_agent(ua_string):
    ua = ua_string or ""
    ua_l = ua.lower()

    # Browser
    if "edg/" in ua_l:
        browser = "Edge"
    elif "chrome/" in ua_l and "chromium" not in ua_l:
        browser = "Chrome"
    elif "firefox/" in ua_l:
        browser = "Firefox"
    elif "safari/" in ua_l and "chrome" not in ua_l:
        browser = "Safari"
    elif "opr/" in ua_l or "opera" in ua_l:
        browser = "Opera"
    else:
        browser = "Unknown"

    # OS
    if "windows nt" in ua_l:
        os_name = "Windows"
    elif "mac os x" in ua_l:
        os_name = "macOS"
    elif "android" in ua_l:
        os_name = "Android"
    elif "iphone" in ua_l or "ipad" in ua_l:
        os_name = "iOS"
    elif "linux" in ua_l:
        os_name = "Linux"
    else:
        os_name = "Unknown"

    # Device
    if "mobile" in ua_l and "ipad" not in ua_l:
        device = "Mobile"
    elif "ipad" in ua_l or "tablet" in ua_l:
        device = "Tablet"
    else:
        device = "Desktop"

    return {"browser": browser, "os": os_name, "device": device}


def get_client_ip(request):
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"
