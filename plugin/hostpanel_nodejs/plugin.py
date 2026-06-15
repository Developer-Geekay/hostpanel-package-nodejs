from hostpanel_nodejs.apps import router


PLUGIN_MANIFEST = {
    "requires_core": [1, 0, 0],
    "repository": "https://github.com/Developer-Geekay/hostpanel-package-nodejs",
    "nav_items": [
        {
            "nav_route": "nodejs",
            "nav_label": "Node.js",
            "nav_icon": "terminal",
            "nav_section": "hosting",
            "nav_section_label": "Hosting",
            "nav_section_order": 20,
            "admin_only": False,
        },
    ],
    "dashboard_blocks": [
        {
            "type": "stat",
            "label": "Node Apps",
            "icon": "terminal",
            "endpoint": "nodejs/count",
            "size": "sm",
        },
    ],
    "service": {
        "name": "nodejs",
        "unit": "hostpanel-nodejs",
        "label": "Node.js Apps",
        "icon": "terminal",
        "can_reload": False,
    },
}


routers = [router]
