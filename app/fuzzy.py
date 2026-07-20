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


def find_matches_batch(
    queries: list[str],
    candidates: list[str],
    threshold: int = DEFAULT_THRESHOLD,
) -> dict[str, str | None]:
    """
    Batch version — builds the candidate index once instead of per-query,
    which matters once your collection is a few thousand cards and you're
    matching a 100-line decklist against it.
    """
    return {q: find_best_match(q, candidates, threshold) for q in queries}
