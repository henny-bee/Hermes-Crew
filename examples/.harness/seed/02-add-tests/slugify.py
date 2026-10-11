import re


def slugify(text, max_len=None):
    """Lower-case URL slug: letters and digits separated by single dashes."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if max_len is not None:
        slug = slug[:max_len]
    return slug


def parse_duration(text):
    """'1h30m' -> 5400, '45s' -> 45, '2d' -> 172800. Raises ValueError on anything else."""
    units = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    parts = re.findall(r"(\d+)([dhms])", text)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != text:
        raise ValueError(f"bad duration: {text!r}")
    return sum(int(n) * units[u] for n, u in parts)
