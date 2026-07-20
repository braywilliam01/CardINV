import re
from dataclasses import dataclass


@dataclass
class ParsedLine:
    raw_line: str          # original text, untouched
    quantity: int
    card_name: str
    valid: bool             # False if line couldn't be parsed at all


# Matches a leading quantity in either "4 " or "4x " form
_QTY_PREFIX = re.compile(r"^\s*(\d+)\s*x?\s+", re.IGNORECASE)

# Matches a trailing quantity in "Card Name x4" form (less common, some TCGplayer exports)
_QTY_SUFFIX = re.compile(r"\s+x?(\d+)\s*$", re.IGNORECASE)

# Trailing set/collector/foil junk to strip once quantity is removed, e.g.:
#   (CLB) 304
#   [CLB] 133
#   *F*
#   (Foil)
#   CLB-304
_TRAILING_JUNK = re.compile(
    r"""
    \s*
    (
        \(\w{2,5}\)\s*\d*\w*      |  # (CLB) 304  or (CLB)
        \[\w{2,5}\]\s*\d*\w*      |  # [CLB] 133
        \b\w{2,5}-\d+\w*          |  # CLB-304
        \*F\*                      |  # *F*
        \(?[Ff]oil\)?              |  # Foil / (Foil)
        \b[Ee]tched\b              |
        \bsurge\s*foil\b
    )
    \s*$
    """,
    re.VERBOSE,
)

MAX_LINES = 100
MAX_LINE_LENGTH = 50


def parse_line(line: str) -> ParsedLine:
    """Parse a single decklist line into quantity + card name."""
    raw = line.rstrip("\n")
    stripped = raw.strip()

    if not stripped:
        return ParsedLine(raw_line=raw, quantity=0, card_name="", valid=False)

    working = stripped
    quantity = None

    # Try leading quantity first (most common: "4 Lightning Bolt ...")
    match = _QTY_PREFIX.match(working)
    if match:
        quantity = int(match.group(1))
        working = working[match.end():]
    else:
        # Fall back to trailing quantity ("Lightning Bolt x4")
        match = _QTY_SUFFIX.search(working)
        if match:
            quantity = int(match.group(1))
            working = working[: match.start()]

    if quantity is None:
        # No quantity found at all — treat as invalid rather than guessing "1"
        return ParsedLine(raw_line=raw, quantity=0, card_name="", valid=False)

    # Strip trailing set code / collector number / foil markers.
    # Run twice: e.g. "Lightning Bolt (CLB) 304 *F*" has two trailing chunks.
    for _ in range(2):
        new_working = _TRAILING_JUNK.sub("", working)
        if new_working == working:
            break
        working = new_working

    card_name = working.strip()

    if not card_name:
        return ParsedLine(raw_line=raw, quantity=quantity, card_name="", valid=False)

    return ParsedLine(raw_line=raw, quantity=quantity, card_name=card_name, valid=True)


def parse_decklist(text: str) -> list[ParsedLine]:
    """
    Parse a multi-line decklist. Enforces the 100-line / 50-char-per-line
    limits from the spec; lines beyond those limits are marked invalid
    rather than silently dropped, so the caller can surface an error.
    """
    lines = text.splitlines()[:MAX_LINES]
    results = []

    for line in lines:
        if len(line) > MAX_LINE_LENGTH:
            results.append(
                ParsedLine(raw_line=line, quantity=0, card_name="", valid=False)
            )
            continue
        results.append(parse_line(line))

    return results
