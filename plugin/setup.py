from setuptools import find_packages, setup


setup(
    name="hostpanel-nodejs",
    version="1.8.0",
    packages=find_packages(),
    package_data={"hostpanel_nodejs": ["data/*"]},
    include_package_data=True,
    install_requires=["fastapi", "pydantic", "PyJWT[crypto]"],
    entry_points={
        "hostpanel.modules": [
            "nodejs = hostpanel_nodejs.plugin",
        ],
        "hostpanel.setup": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_install",
        ],
        "hostpanel.lifecycle": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:pre_uninstall",
        ],
        "hostpanel.hooks.on_startup": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_startup",
        ],
        "hostpanel.hooks.user_delete": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_user_delete",
        ],
        "hostpanel.hooks.domain_delete": [
            "hostpanel-nodejs = hostpanel_nodejs.lifecycle:on_domain_delete",
        ],
    },
)
