"""Local coding agent package."""


def create_app(*args, **kwargs):
    """Load the optional web layer only when the server is started."""
    from .web import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["create_app"]
