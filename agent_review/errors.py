"""Review failures stay distinct from ordinary input errors."""


class ReviewError(ValueError):
    """The command cannot be performed. Exit 2."""


class ReviewIncomplete(Exception):
    """Formal reports were requested before the review finished. Exit 3."""
