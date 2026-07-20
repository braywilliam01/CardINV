import re
from dataclasses import dataclass


@dataclass
class ParsedLine:
    raw_line: str          # original text, untouched
    quantity: int
    card_name: str
    valid: bool             # False if line couldn't be parsed at all
    set_code: str = ""            # "" = unpinned (no printing specified)
    collector_number: str = ""    # "" = unpinned


# Matches a leading quantity in either "4 " or "4x " form
_QTY_PREFIX = re.compile(r"^\s*(\d+)\s*x?\s+", re.IGNORECASE)

# Matches a trailing quantity in "Card Name x4" form (less common, some TCGplayer exports)
_QTY_SUFFIX = re.compile(r"\s+x?(\d+)\s*$", re.IGNORECASE)

# A trailing printing reference with BOTH a set and a collector number —
# specific enough to pin an exact printing, e.g.:
#   (CLB) 304
#   [CLB] 133
#   CLB-304
# Captured (not just discarded) so checkout/checkin can target that exact
# printing instead of falling back to the cheapest-first draw-down — this
# is what makes a deck's contents round-trip through paste/edit/paste.
_PRINTING_SUFFIX = re.compile(
    r"""
    \s*
    (?:
        \((?P<set1>[A-Za-z0-9]{2,5})\)\s*(?P<num1>\d+\w*)   |  # (CLB) 304
        \[(?P<set2>[A-Za-z0-9]{2,5})\]\s*(?P<num2>\d+\w*)   |  # [CLB] 133
        \b(?P<set3>[A-Za-z0-9]{2,5})-(?P<num3>\d+\w*)           # CLB-304
    )
    \s*$
    """,
    re.VERBOSE,
)

# Trailing junk with no usable printing identity — a bare set code with no
# number can't pin an exact printing (which specific one?), so it's
# stripped the same as foil/finish markers rather than captured.
_STRIPPABLE_JUNK = re.compile(
    r"""
    \s*
    (
        \(\w{2,5}\)                |  # (CLB) with no collector number
        \[\w{2,5}\]                |  # [CLB] with no collector number
        \*F\*                      |
        \(?[Ff]oil\)?              |
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
    """Parse a single decklist line into quantity + card name (+ an
    optional pinned printing)."""
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

    # Strip trailing set code / collector number / foil markers, capturing
    # a printing pin when one's specific enough to identify. Loop since a
    # line can carry more than one trailing chunk, e.g.
    # "Lightning Bolt (CLB) 304 *F*" needs two passes.
    set_code = ""
    collector_number = ""
    for _ in range(3):
        pin_match = _PRINTING_SUFFIX.search(working)
        if pin_match:
            set_code = (pin_match.group("set1") or pin_match.group("set2") or pin_match.group("set3")).upper()
            collector_number = pin_match.group("num1") or pin_match.group("num2") or pin_match.group("num3")
            working = working[: pin_match.start()]
            continue

        new_working = _STRIPPABLE_JUNK.sub("", working)
        if new_working != working:
            working = new_working
            continue

        break

    card_name = working.strip()

    if not card_name:
        return ParsedLine(raw_line=raw, quantity=quantity, card_name="", valid=False)

    return ParsedLine(
        raw_line=raw, quantity=quantity, card_name=card_name, valid=True,
        set_code=set_code, collector_number=collector_number,
    )


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
