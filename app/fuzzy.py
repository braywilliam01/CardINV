from rapidfuzz import fuzz, process

DEFAULT_THRESHOLD = 85  # 0-100 scale; tune per taste


def find_best_match(
    query: str,
    candidates: list[str],
    threshold: int = DEFAULT_THRESHOLD,
) -> str | None:
    """
    Return the best-matching card name from `candidates` for `query`,
    or None if nothing clears the threshold.
    """
    if not candidates:
        return None

    result = process.extractOne(
        query,
        candidates,
        scorer=fuzz.WRatio,  # handles word order / partial token differences well
        score_cutoff=threshold,
    )
    return result[0] if result else None
