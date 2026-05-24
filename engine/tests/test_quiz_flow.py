"""
Pure unit tests for engine/quiz_flow.py.

No DB, no LLM, no Django setup required.
Skills are mocked with SimpleNamespace.
"""
import pytest
from types import SimpleNamespace
from engine.quiz_flow import (
    quiz_initial_state,
    quiz_select_skill_idx,
    quiz_update_state,
    quiz_is_done,
    quiz_derive_results,
)


def make_skills(count, cefr_seq=None):
    """Create a list of fake skill objects with cefr_level cycling through cefr_seq."""
    levels = cefr_seq or ['A1', 'A1', 'A2', 'A2', 'B1', 'B1', 'B2', 'B2', 'C1', 'C1', 'C2']
    return [
        SimpleNamespace(skill_id=f'skill_{i}', cefr_level=levels[i % len(levels)])
        for i in range(count)
    ]


def apply_scores(state, idx_score_pairs):
    """Apply a sequence of (idx, score) pairs to the state, simulating quiz turns."""
    for idx, score in idx_score_pairs:
        state = quiz_update_state(state, idx, score)
    return state


# ── Initial state ─────────────────────────────────────────────────────────────

def test_initial_state_step_size_10_percent():
    state = quiz_initial_state(80)
    assert state["step_size"] == 8

def test_initial_state_step_size_minimum_1():
    state = quiz_initial_state(5)
    assert state["step_size"] == 1

def test_initial_state_pass_is_1():
    state = quiz_initial_state(20)
    assert state["pass"] == 1
    assert state["question_count"] == 0
    assert state["last_pass_idx"] == -1


# ── quiz_select_skill_idx in Pass 1 ──────────────────────────────────────────

def test_select_idx_pass1_starts_at_zero():
    state = quiz_initial_state(20)
    assert quiz_select_skill_idx(state) == 0

def test_select_idx_pass1_after_pass_advances_by_step():
    state = quiz_initial_state(20)  # step=2
    state = quiz_update_state(state, 0, 4)   # PASS
    assert quiz_select_skill_idx(state) == 2

def test_select_idx_pass1_after_one_fail_advances_by_step():
    state = quiz_initial_state(20)  # step=2
    state = quiz_update_state(state, 0, 2)   # FAIL (first)
    assert quiz_select_skill_idx(state) == 2


# ── Pass 1: gap detection and exit ────────────────────────────────────────────

def test_pass1_single_fail_records_gap_and_continues():
    state = quiz_initial_state(20)  # step=2
    state = quiz_update_state(state, 0, 4)   # PASS idx=0
    state = quiz_update_state(state, 2, 2)   # FAIL idx=2 → gap recorded
    assert state["pass"] == 1
    assert len(state["gaps"]) == 1
    assert state["gaps"][0] == {"last_pass_idx": 0, "first_fail_idx": 2}

def test_pass1_two_consecutive_fails_exits_to_pass2():
    state = quiz_initial_state(20)  # step=2
    state = quiz_update_state(state, 0, 4)   # PASS
    state = quiz_update_state(state, 2, 2)   # FAIL 1
    state = quiz_update_state(state, 4, 1)   # FAIL 2 → exit
    assert state["pass"] == 2

def test_pass1_all_passing_skips_to_done():
    state = quiz_initial_state(10)  # step=1
    # Pass every skill (0 through 9)
    for i in range(10):
        state = quiz_update_state(state, i, 4)
    assert state["pass"] == "done"

def test_pass1_no_gaps_at_all_goes_directly_to_done():
    """If pass 1 completes with no gaps detected at all, quiz is done."""
    state = quiz_initial_state(5)  # step=1
    for i in range(5):
        state = quiz_update_state(state, i, 4)  # all pass
    assert quiz_is_done(state)

def test_pass1_fail_at_start_records_gap_with_last_pass_minus1():
    state = quiz_initial_state(10)  # step=1
    state = quiz_update_state(state, 0, 1)   # FAIL at very first probe
    assert state["gaps"][0]["last_pass_idx"] == -1
    assert state["gaps"][0]["first_fail_idx"] == 0


# ── Pass 2: bisection ─────────────────────────────────────────────────────────

def _state_in_pass2(last_pass, first_fail):
    """Helper: create state already in Pass 2 with one gap."""
    state = quiz_initial_state(100)  # step=10
    state["pass"] = 2
    state["gaps"] = [{"last_pass_idx": last_pass, "first_fail_idx": first_fail}]
    state["current_gap_idx"] = 0
    # Prime scores dict with the boundary probes so window detection sees them
    state["scores"][str(last_pass)] = 4
    state["scores"][str(first_fail)] = 1
    state["question_count"] = 2
    return state

def test_pass2_bisection_narrows_on_pass():
    state = _state_in_pass2(10, 20)
    mid = (10 + 20) // 2  # 15
    state = quiz_update_state(state, mid, 4)   # PASS
    assert state["gaps"][0]["last_pass_idx"] == mid

def test_pass2_bisection_narrows_on_fail():
    state = _state_in_pass2(10, 20)
    mid = (10 + 20) // 2  # 15
    state = quiz_update_state(state, mid, 1)   # FAIL
    assert state["gaps"][0]["first_fail_idx"] == mid

def test_pass2_resolves_single_gap_then_transitions_to_pass3():
    """After single gap is resolved in Pass 2, should move to Pass 3."""
    state = _state_in_pass2(10, 13)
    # Score 11 and 12 to get 3 consecutive around boundary
    state = quiz_update_state(state, 11, 4)
    state = quiz_update_state(state, 12, 1)
    assert state["pass"] == 3

def test_pass2_advances_to_next_gap_when_multiple_gaps():
    state = quiz_initial_state(100)
    state["pass"] = 2
    state["gaps"] = [
        {"last_pass_idx": 5, "first_fail_idx": 8},
        {"last_pass_idx": 40, "first_fail_idx": 50},
    ]
    state["current_gap_idx"] = 0
    state["scores"] = {"5": 4, "8": 1}
    state["question_count"] = 2
    # Resolve first gap: score 6 and 7
    state = quiz_update_state(state, 6, 4)
    state = quiz_update_state(state, 7, 1)
    assert state["pass"] == 2
    assert state["current_gap_idx"] == 1

def test_pass2_to_pass3_selects_gap_with_highest_first_fail():
    # Use narrow gaps so they resolve quickly (ff - lp == 1 → done after 1 probe)
    state = quiz_initial_state(100)
    state["pass"] = 2
    state["gaps"] = [
        {"last_pass_idx": 5,  "first_fail_idx": 7},
        {"last_pass_idx": 40, "first_fail_idx": 42},
    ]
    state["current_gap_idx"] = 0
    state["scores"] = {"5": 4, "7": 1, "40": 4, "42": 1}
    state["question_count"] = 4
    # Resolve first gap: probe midpoint 6
    state = quiz_update_state(state, 6, 4)   # gap becomes {lp:6, ff:7} → ff-lp=1 → done
    assert state["current_gap_idx"] == 1     # advanced to gap 1
    # Resolve second gap: probe midpoint 41
    state = quiz_update_state(state, 41, 4)  # gap becomes {lp:41, ff:42} → ff-lp=1 → done
    # Now in pass 3; primary border should be gap index 1 (highest first_fail=42)
    assert state["pass"] == 3
    assert state["primary_border_gap_idx"] == 1


# ── Pass 3 ────────────────────────────────────────────────────────────────────

def _state_in_pass3(last_pass, first_fail):
    state = quiz_initial_state(100)
    state["pass"] = 3
    state["gaps"] = [{"last_pass_idx": last_pass, "first_fail_idx": first_fail}]
    state["primary_border_gap_idx"] = 0
    state["scores"] = {str(last_pass): 4, str(first_fail): 1}
    state["question_count"] = 2
    return state

def test_pass3_bisects_gap():
    state = _state_in_pass3(20, 30)
    idx = quiz_select_skill_idx(state)
    assert idx == 25

def test_pass3_done_when_3_consecutive_scored():
    state = _state_in_pass3(20, 23)
    state = quiz_update_state(state, 21, 4)
    state = quiz_update_state(state, 22, 1)
    assert state["pass"] == "done"

def test_pass3_done_flag_propagates_to_quiz_is_done():
    state = _state_in_pass3(20, 23)
    state = quiz_update_state(state, 21, 4)
    state = quiz_update_state(state, 22, 1)
    assert quiz_is_done(state)


# ── Hard cap ──────────────────────────────────────────────────────────────────

def test_quiz_is_done_at_20_questions():
    state = quiz_initial_state(100)
    state["question_count"] = 20
    assert quiz_is_done(state)

def test_quiz_not_done_at_19_questions():
    state = quiz_initial_state(100)
    state["question_count"] = 19
    assert not quiz_is_done(state)


# ── quiz_derive_results ───────────────────────────────────────────────────────

def test_derive_results_returns_cefr_of_last_passing_skill():
    skills = make_skills(10, ['A1', 'A1', 'A2', 'A2', 'B1', 'B1', 'B2', 'B2', 'C1', 'C1'])
    state = quiz_initial_state(10)
    state["scores"] = {"0": 4, "1": 4, "2": 4, "3": 4, "4": 3, "5": 2}
    result = quiz_derive_results(state, skills)
    assert result["cefr_level"] == "B1"  # skill 4 is B1 and scored 3

def test_derive_results_all_fail_returns_a1():
    skills = make_skills(5, ['A1', 'A2', 'B1', 'B2', 'C1'])
    state = quiz_initial_state(5)
    state["scores"] = {"0": 1, "2": 1, "4": 1}
    result = quiz_derive_results(state, skills)
    assert result["cefr_level"] == "A1"

def test_derive_results_all_pass_returns_highest_cefr():
    skills = make_skills(5, ['A1', 'A2', 'B1', 'B2', 'C1'])
    state = quiz_initial_state(5)
    state["scores"] = {"0": 4, "1": 4, "2": 4, "3": 4, "4": 4}
    result = quiz_derive_results(state, skills)
    assert result["cefr_level"] == "C1"

def test_derive_results_populates_skill_scores_dict():
    skills = make_skills(3)
    state = quiz_initial_state(3)
    state["scores"] = {"0": 4, "2": 2}
    result = quiz_derive_results(state, skills)
    assert result["skill_scores"]["skill_0"] == 4
    assert result["skill_scores"]["skill_2"] == 2
    assert "skill_1" not in result["skill_scores"]

def test_derive_results_no_scores_returns_a1():
    skills = make_skills(5)
    state = quiz_initial_state(5)
    result = quiz_derive_results(state, skills)
    assert result["cefr_level"] == "A1"
    assert result["skill_scores"] == {}
