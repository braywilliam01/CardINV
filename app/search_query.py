import re

# Matches a trailing "SET NUMBER" printing reference in Card Search's
# free-text input, e.g. "CLB 304" — a set code (letters and/or digits;
# some real codes are alphanumeric, e.g. "40k") followed by whitespace
# and a collector number. The number MUST start with a digit — without
# that, a plain two-word card name with no comma (e.g. "Sol Ring") would
# itself match "SET NUMBER" (SOL + Ring) and get misparsed as a printing
# reference instead of searched by name. An optional leading "#" is
# tolerated on the number, since the UI's own help text shows the format
# as "SET #" and users understandably type that "#" literally.
_PRINTING_QUERY = re.compile(r"^([A-Za-z0-9]{2,5})\s+#?(\d+\S*)$")


def parse_search_query(query: str) -> tuple[str, str, str]:
    """
    Parses Card Search's input for an optional exact-printing reference,
    so a search can pin one specific card instead of relying on a fuzzy
    name match. Shared by both games' lookup_card:
      "Lightning Bolt, CLB 304"  -> ("Lightning Bolt", "CLB", "304")
      "CLB 304"                  -> ("", "CLB", "304")
      "Lightning Bolt"           -> ("Lightning Bolt", "", "")  (fuzzy, as before)

    set_code/collector_number are "" when no printing reference was
    recognized. Many real card names contain a comma of their own (e.g.
    "Urza, Lord High Artificer") — splits on the *last* comma (so a
    printing suffix still works after one of those, e.g. "Jhoira,
    Weatherlight Captain, CLB 5") and, if what follows doesn't actually
    parse as "SET NUMBER", assumes the comma belongs to the name itself
    and returns the *whole* original query untouched rather than
    truncating it.
    """
    query = query.strip()

    if "," in query:
        name_part, _, printing_part = query.rpartition(",")
        match = _PRINTING_QUERY.match(printing_part.strip())
        if match:
            return name_part.strip(), match.group(1).upper(), match.group(2)
        return query, "", ""

    match = _PRINTING_QUERY.match(query)
    if match:
        return "", match.group(1).upper(), match.group(2)

    return query, "", ""
