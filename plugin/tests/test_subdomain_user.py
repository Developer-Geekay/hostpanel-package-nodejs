"""Tests for subdomain user resolution in hostpanel-package-nodejs.
"""
import sys
from hostpanel_nodejs import validators


def test_resolve_domain_user_main_domain(monkeypatch):
    monkeypatch.setattr(validators, "_load_domains", lambda: [
        {"domain_name": "consoleapi.in", "username": "consoleapi", "document_root": "/home/consoleapi/public_html"}
    ])
    monkeypatch.setattr(validators, "_load_subdomains", lambda: [])

    user = validators.resolve_domain_user("consoleapi.in")
    assert user == "consoleapi"


def test_resolve_domain_user_subdomain_uses_main_domain_user(monkeypatch):
    monkeypatch.setattr(validators, "_load_domains", lambda: [
        {"domain_name": "consoleapi.in", "username": "consoleapi", "document_root": "/home/consoleapi/public_html"}
    ])
    monkeypatch.setattr(validators, "_load_subdomains", lambda: [
        {"fqdn": "products.consoleapi.in", "subdomain": "products", "parent_domain": "consoleapi.in", "username": "products"}
    ])

    user = validators.resolve_domain_user("products.consoleapi.in")
    assert user == "consoleapi"


def test_resolve_domain_user_nested_subdomain(monkeypatch):
    monkeypatch.setattr(validators, "_load_domains", lambda: [
        {"domain_name": "consoleapi.in", "username": "consoleapi", "document_root": "/home/consoleapi/public_html"}
    ])
    monkeypatch.setattr(validators, "_load_subdomains", lambda: [
        {"fqdn": "prod.products.consoleapi.in", "subdomain": "prod.products", "parent_domain": "products.consoleapi.in", "username": "prod"}
    ])

    user = validators.resolve_domain_user("console-prod.products.consoleapi.in")
    assert user == "consoleapi"


def test_eligible_domains_subdomain_ownership(monkeypatch):
    monkeypatch.setattr(validators, "_load_domains", lambda: [
        {"domain_name": "consoleapi.in", "username": "consoleapi", "document_root": "/home/consoleapi/public_html"}
    ])
    monkeypatch.setattr(validators, "_load_subdomains", lambda: [
        {"fqdn": "products.consoleapi.in", "subdomain": "products", "parent_domain": "consoleapi.in", "username": "products"}
    ])

    user = sys.modules["auth"].User(username="consoleapi", role="user", linux_user="consoleapi")
    options = validators.eligible_domains(user)
    assert len(options) == 2
    sub = next(opt for opt in options if opt["domain"] == "products.consoleapi.in")
    assert sub["username"] == "consoleapi"
