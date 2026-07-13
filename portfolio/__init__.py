"""Event pipeline application package.

Importing :mod:`portfolio` must stay side-effect free. ASGI servers should
load ``portfolio.main:app`` explicitly; workers import shared modules from this
package without constructing the FastAPI application or mounting static files.
"""
