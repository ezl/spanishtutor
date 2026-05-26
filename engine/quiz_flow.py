"""
Pure deterministic functions for the three-pass placement quiz probe algorithm.

No DB access, no LLM calls — fully unit-testable without Django setup.

Pass 1 — Terrain scan:
  Probe indices 0, step, 2*step… through the full skill ladder.
  Record gap when a failure follows a pass. Two consecutive failures → exit Pass 1.

Pass 2 — Gap bisection (all detected gaps):
  For each gap [last_pass_idx, first_fail_idx]: probe midpoint.
  Exit when 3 or 4 consecutive skill indices have been scored around the boundary.

Pass 3 — Primary border refinement (highest gap only):
  Same bisection mechanic. Applied to the gap with the highest first_fail_idx.
  Exit condition identical to Pass 2.

Hard cap: 20 questions total.

Score → navigation: score >= 3 = PASS, score <= 2 = FAIL.
CEFR level = cefr_level of the last skill with score >= 3. Fallback: 'A1'.
"""


def quiz_initial_state(skill_count: int) -> dict:
    step = max(1, skill_count // 10)
    return {
        "pass": 1,
        "skill_count": skill_count,
        "step_size": step,
        "current_idx": 0,
        "last_pass_idx": -1,
        "consecutive_fails": 0,
        "gaps": [],
        "current_gap_idx": 0,
        "primary_border_gap_idx": None,
        "scores": {},
        "asked_question_ids": [],
        "current_question_id": None,
        "current_skill_idx": None,
        "question_count": 0,
    }


def quiz_select_skill_idx(state: dict) -> int:
    """Return the skill ladder index to probe next, given the current state."""
    p = state["pass"]
    if p == 1:
        return state["current_idx"]
    if p in (2, 3):
        gaps = state["gaps"]
        if p == 2:
            gap_idx = state["current_gap_idx"]
        else:
            gap_idx = state["primary_border_gap_idx"]
        gap = gaps[gap_idx]
        lp = gap["last_pass_idx"]
        ff = gap["first_fail_idx"]
        midpoint = (lp + ff) // 2
        if midpoint == lp:
            midpoint = lp + 1
        return midpoint
    raise ValueError(f"quiz_select_skill_idx called with pass='{p}' (should be 1, 2, or 3)")


def quiz_update_state(state: dict, skill_idx: int, score: int) -> dict:
    """
    Record the score and advance the pass state machine.
    Returns updated state (mutates and returns the same dict).
    """
    state = dict(state)  # shallow copy at top level
    state["scores"] = dict(state["scores"])
    state["gaps"] = [dict(g) for g in state["gaps"]]
    state["scores"][str(skill_idx)] = score
    state["question_count"] += 1
    passed = score >= 3

    p = state["pass"]

    if p == 1:
        state = _pass1_update(state, skill_idx, passed)
    elif p == 2:
        state = _pass2_update(state, skill_idx, passed)
    elif p == 3:
        state = _pass3_update(state, skill_idx, passed)

    return state


def _pass1_update(state: dict, skill_idx: int, passed: bool) -> dict:
    step = state["step_size"]
    skill_count = state["skill_count"]

    if passed:
        state["last_pass_idx"] = skill_idx
        state["consecutive_fails"] = 0
        next_idx = skill_idx + step
        if next_idx >= skill_count:
            # Reached end of ladder with no second failure — transition now
            state = _transition_to_pass_2(state)
        else:
            state["current_idx"] = next_idx
    else:
        state["consecutive_fails"] += 1
        if state["consecutive_fails"] == 1:
            # First failure — record gap and continue forward
            gap = {"last_pass_idx": state["last_pass_idx"], "first_fail_idx": skill_idx}
            state["gaps"].append(gap)
            next_idx = skill_idx + step
            if next_idx >= skill_count:
                state = _transition_to_pass_2(state)
            else:
                state["current_idx"] = next_idx
        else:
            # Second consecutive failure → exit Pass 1
            state = _transition_to_pass_2(state)

    return state


def _pass2_update(state: dict, skill_idx: int, passed: bool) -> dict:
    gap_idx = state["current_gap_idx"]
    gap = state["gaps"][gap_idx]

    if passed:
        gap["last_pass_idx"] = skill_idx
    else:
        gap["first_fail_idx"] = skill_idx
    state["gaps"][gap_idx] = gap

    if _consecutive_window_done(state, gap):
        # This gap is resolved; move to next gap or transition to Pass 3
        next_gap_idx = gap_idx + 1
        if next_gap_idx < len(state["gaps"]):
            state["current_gap_idx"] = next_gap_idx
        else:
            state = _transition_to_pass_3(state)

    return state


def _pass3_update(state: dict, skill_idx: int, passed: bool) -> dict:
    gap_idx = state["primary_border_gap_idx"]
    gap = state["gaps"][gap_idx]

    if passed:
        gap["last_pass_idx"] = skill_idx
    else:
        gap["first_fail_idx"] = skill_idx
    state["gaps"][gap_idx] = gap

    if _consecutive_window_done(state, gap):
        state["pass"] = "done"

    return state


def _consecutive_window_done(state: dict, gap: dict) -> bool:
    """True when 3 or 4 consecutive skill indices around the gap boundary have been scored."""
    lp = gap["last_pass_idx"]
    ff = gap["first_fail_idx"]
    # Count how many indices in the range [lp, ff] (inclusive) have been scored
    scored = state["scores"]
    # The boundary region is the indices between lp and ff (inclusive)
    count = sum(1 for i in range(max(0, lp), ff + 1) if str(i) in scored)
    # ff - lp is the gap width; when <= 3 we have enough consecutive coverage
    return count >= 3 or (ff - lp) <= 1


def _transition_to_pass_2(state: dict) -> dict:
    if not state["gaps"]:
        # No gaps detected — sailed through entire ladder
        state["pass"] = "done"
        return state
    state["pass"] = 2
    state["current_gap_idx"] = 0
    return state


def _transition_to_pass_3(state: dict) -> dict:
    if not state["gaps"]:
        state["pass"] = "done"
        return state
    # Primary border = gap with the highest first_fail_idx
    primary_idx = max(range(len(state["gaps"])), key=lambda i: state["gaps"][i]["first_fail_idx"])
    # If the primary gap is already resolved (ff - lp <= 1), no further probing needed
    if _consecutive_window_done(state, state["gaps"][primary_idx]):
        state["pass"] = "done"
        return state
    state["pass"] = 3
    state["primary_border_gap_idx"] = primary_idx
    return state


def quiz_is_done(state: dict) -> bool:
    return state["pass"] == "done" or state["question_count"] >= 20


def quiz_derive_results(state: dict, skills: list) -> dict:
    """
    skills: ordered list of Skill objects (same ordering as the probe ladder).
    Returns {cefr_level: str, skill_scores: {skill_id: score_int}}.
    CEFR level = cefr_level of the last skill with score >= 3. Fallback: 'A1'.
    """
    scores = state["scores"]
    cefr_level = "A1"
    skill_scores = {}

    for idx, skill in enumerate(skills):
        raw = scores.get(str(idx))
        if raw is not None:
            skill_scores[skill.skill_id] = raw
            if raw >= 3:
                cefr_level = skill.cefr_level

    return {"cefr_level": cefr_level, "skill_scores": skill_scores}
