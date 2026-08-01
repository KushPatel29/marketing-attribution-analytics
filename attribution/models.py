"""
The two attribution models that need more than a window function: a Markov
chain removal effect, and an exact Shapley value.

Both read only observed journeys — the path a user took and whether they
converted. Neither is told anything about the generator's true lifts. That
separation is what makes the grading in `evaluate.py` legitimate: the models
see exactly what a real marketing team sees.
"""

from __future__ import annotations

from itertools import combinations
from math import factorial

import numpy as np
import pandas as pd

START, CONV, NULL = "(start)", "(conversion)", "(null)"


# ------------------------------------------------------------------- markov
def build_transition_matrix(
    paths: list[list[str]], converted: list[int], channels: list[str]
) -> tuple[np.ndarray, list[str]]:
    """
    Count transitions across every journey and row-normalise into a Markov
    chain over {start, channels..., conversion, null}.

    Repeated consecutive touches on the same channel are kept as self-loops
    rather than collapsed. Collapsing them is a defensible modelling choice,
    but it quietly deletes evidence of retargeting frequency, so this keeps
    them and lets the absorbing-state maths handle it.
    """
    states = [START, *channels, CONV, NULL]
    index = {s: i for i, s in enumerate(states)}
    n = len(states)
    counts = np.zeros((n, n), dtype=float)

    for path, conv in zip(paths, converted, strict=True):
        prev = index[START]
        for ch in path:
            cur = index[ch]
            counts[prev, cur] += 1
            prev = cur
        counts[prev, index[CONV if conv else NULL]] += 1

    # Absorbing states point only at themselves.
    counts[index[CONV], :] = 0.0
    counts[index[NULL], :] = 0.0
    counts[index[CONV], index[CONV]] = 1.0
    counts[index[NULL], index[NULL]] = 1.0

    row = counts.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        probs = np.divide(counts, row, out=np.zeros_like(counts), where=row > 0)
    return probs, states


def conversion_probability(probs: np.ndarray, states: list[str]) -> float:
    """
    Probability of absorbing in (conversion) starting from (start).

    Solved directly with the fundamental matrix N = (I - Q)^-1 rather than by
    iterating the chain, so the number is exact instead of "close enough after
    a thousand steps".
    """
    conv_i, null_i = states.index(CONV), states.index(NULL)
    transient = [i for i in range(len(states)) if i not in (conv_i, null_i)]
    q = probs[np.ix_(transient, transient)]
    r = probs[np.ix_(transient, [conv_i, null_i])]
    n_mat = np.linalg.inv(np.eye(len(transient)) - q)
    absorb = n_mat @ r
    return float(absorb[transient.index(states.index(START)), 0])


def markov_removal_effect(
    paths: list[list[str]], converted: list[int], channels: list[str]
) -> pd.DataFrame:
    """
    Removal effect: knock a channel out of the graph, send everything that
    pointed at it to (null), and see how much conversion probability the whole
    system loses.

    This is the closest any observational model gets to asking the
    counterfactual question the ground truth answers directly — which is why
    it is the one to beat.
    """
    probs, states = build_transition_matrix(paths, converted, channels)
    base = conversion_probability(probs, states)

    rows = []
    for ch in channels:
        i = states.index(ch)
        removed = probs.copy()
        # Everything that used to flow into the channel now dies.
        null_i = states.index(NULL)
        inflow = removed[:, i].copy()
        removed[:, i] = 0.0
        removed[:, null_i] += inflow
        # The channel itself becomes a dead end.
        removed[i, :] = 0.0
        removed[i, null_i] = 1.0
        p = conversion_probability(removed, states)
        rows.append({"channel": ch, "removal_effect": max(base - p, 0.0) / base})

    df = pd.DataFrame(rows)
    df["markov_share"] = df["removal_effect"] / df["removal_effect"].sum()
    return df


# ------------------------------------------------------------------ shapley
def shapley_values(
    paths: list[list[str]], converted: list[int], channels: list[str]
) -> pd.DataFrame:
    """
    Exact Shapley value over the channel set.

    The coalition worth v(S) is the number of conversions among journeys whose
    channels all lie inside S. With seven channels that is 128 coalitions and
    an exact answer, so there is no reason to reach for a Monte-Carlo
    approximation — and doing so would only add variance to a number we can
    compute outright.
    """
    n = len(channels)
    bit = {ch: 1 << i for i, ch in enumerate(channels)}

    # One bitmask per journey, plus its conversion count.
    masks: dict[int, int] = {}
    for path, conv in zip(paths, converted, strict=True):
        if not conv:
            continue
        m = 0
        for ch in path:
            m |= bit[ch]
        masks[m] = masks.get(m, 0) + 1

    # v(S) for every coalition: conversions whose mask is a subset of S.
    worth = np.zeros(1 << n)
    for s in range(1 << n):
        total = 0
        for m, c in masks.items():
            if m & ~s == 0:
                total += c
        worth[s] = total

    values = {ch: 0.0 for ch in channels}
    others = {ch: [c for c in channels if c != ch] for ch in channels}
    for ch in channels:
        b = bit[ch]
        for k in range(n):
            weight = factorial(k) * factorial(n - k - 1) / factorial(n)
            for combo in combinations(others[ch], k):
                s = 0
                for c in combo:
                    s |= bit[c]
                values[ch] += weight * (worth[s | b] - worth[s])

    df = pd.DataFrame(
        {"channel": channels, "shapley_value": [values[c] for c in channels]}
    )
    df["shapley_value"] = df["shapley_value"].clip(lower=0)
    df["shapley_share"] = df["shapley_value"] / df["shapley_value"].sum()
    return df


def load_journeys(path) -> tuple[list[list[str]], list[int]]:
    df = pd.read_csv(path)
    paths = [p.split(">") for p in df["path"]]
    return paths, df["converted"].tolist()
