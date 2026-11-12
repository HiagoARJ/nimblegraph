"""Token estimation (conservative: ~3.5 characters per token)."""

CHARS_PER_TOKEN = 3.5


def estimate(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def from_size(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def fmt(n: int) -> str:
    """1234 -> '1.2k', 980 -> '980'."""
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)
