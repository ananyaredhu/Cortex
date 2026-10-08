"""ACL labels on derived artifacts (acl-model.md, "Labels on derived artifacts").

An artifact derived from several documents may be served only to someone authorized for **every** one of them.
A flat intersection of their tokens would usually be empty across platforms (a Jira role and a Slack channel share
no token), so the label keeps one token set per source document and the check is a conjunction of overlaps.
"""
from collections.abc import Iterable


def acl_label(token_sets: Iterable[Iterable[str]]) -> list[list[str]]:
    """One sorted token list per source document, deduplicated. Empty label = derived from nothing."""
    seen: set[tuple[str, ...]] = set()
    out: list[list[str]] = []
    for tokens in token_sets:
        key = tuple(sorted(set(tokens)))
        if key not in seen:
            seen.add(key)
            out.append(list(key))
    return out


def may_serve(asker_tokens: Iterable[str], label: list[list[str]]) -> bool:
    """True when the asker holds a token of every source the artifact was derived from."""
    held = set(asker_tokens)
    return all(held & set(tokens) for tokens in label)
