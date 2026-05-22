"""
Tests for engine/onboarding.py

Two tests here MUST FAIL on current code (documenting known bugs):
  - test_save_skill_updates_creates_score_with_correct_fk  (FK bug)
  - test_quiz_start_does_not_use_persona                   (persona conflict)
"""
import pytest
from asgiref.sync import sync_to_async


# ── Area 2: _parse_quiz_response ─────────────────────────────────────────────

class TestParseQuizResponse:
    def test_continue_with_next_question(self):
        from engine.onboarding import _parse_quiz_response
        raw = "CONTINUE\nSKILL_UPDATES: a1_ser_estar_basic:developing\nNEXT_QUESTION: ¿Cómo te llamas?"
        result = _parse_quiz_response(raw)
        assert result['action'] == 'CONTINUE'
        assert 'developing' in result['SKILL_UPDATES']
        assert result['NEXT_QUESTION'] == '¿Cómo te llamas?'

    def test_conclude(self):
        from engine.onboarding import _parse_quiz_response
        raw = "CONCLUDE\nCEFR_LEVEL: B1\nSKILL_UPDATES: a1_ser_estar_basic:mastered\nCOMMENT: Your grammar is solid."
        result = _parse_quiz_response(raw)
        assert result['action'] == 'CONCLUDE'
        assert result['CEFR_LEVEL'] == 'B1'
        assert result['COMMENT'] == 'Your grammar is solid.'

    def test_multiline_next_question(self):
        from engine.onboarding import _parse_quiz_response
        raw = (
            "CONTINUE\n"
            "SKILL_UPDATES: none\n"
            "NEXT_QUESTION: Ella ________ cansada hoy.\n"
            "*(Translation: She is tired today.)*"
        )
        result = _parse_quiz_response(raw)
        assert '________' in result['NEXT_QUESTION']
        assert 'Translation' in result['NEXT_QUESTION']

    def test_preamble_before_action_is_ignored(self):
        from engine.onboarding import _parse_quiz_response
        raw = "Sure, here we go!\nCONTINUE\nSKILL_UPDATES: none\nNEXT_QUESTION: What does 'gracias' mean?"
        result = _parse_quiz_response(raw)
        assert result['action'] == 'CONTINUE'
        assert result.get('NEXT_QUESTION') == "What does 'gracias' mean?"

    def test_missing_next_question_returns_empty(self):
        from engine.onboarding import _parse_quiz_response
        raw = "CONTINUE\nSKILL_UPDATES: none"
        result = _parse_quiz_response(raw)
        assert result['action'] == 'CONTINUE'
        assert not result.get('NEXT_QUESTION')


# ── Area 3: _classify_input ──────────────────────────────────────────────────

class TestClassifyInput:
    def test_dont_know_phrases(self):
        from engine.onboarding import _classify_input
        assert _classify_input("I don't know") == 'dont_know'
        assert _classify_input("idk") == 'dont_know'
        assert _classify_input("no sé") == 'dont_know'

    def test_rage_words(self):
        from engine.onboarding import _classify_input
        assert _classify_input("this is stupid") == 'rage'
        assert _classify_input("wtf is this") == 'rage'

    def test_long_question_is_off_topic(self):
        from engine.onboarding import _classify_input
        assert _classify_input("What does irregular verb conjugation mean?") == 'off_topic'

    def test_regular_answer(self):
        from engine.onboarding import _classify_input
        assert _classify_input("soy estudiante") == 'answer'
        assert _classify_input("a") == 'answer'

    def test_short_question_is_answer(self):
        """Questions under 15 chars are treated as answers, not off-topic."""
        from engine.onboarding import _classify_input
        assert _classify_input("¿Qué?") == 'answer'


# ── Area 1: _save_skill_updates FK bug ───────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_save_skill_updates_creates_score_with_correct_fk(make_user, make_skill):
    """
    BUG (MUST FAIL): _save_skill_updates passes the string slug directly as
    `skill_id` to update_or_create, but that column is the FK integer PK.
    The SkillScore row is not linked to the correct Skill object.
    Fix: look up Skill by skill_id first, then pass the Skill instance.
    """
    from engine.onboarding import _save_skill_updates
    from learner.models import SkillScore

    user = await sync_to_async(make_user)(discord_id='u_fk1')
    skill = await sync_to_async(make_skill)(skill_id='a1_ser_estar_basic')

    try:
        await _save_skill_updates(user, 'a1_ser_estar_basic:developing')
    except Exception:
        pass  # Bug may raise instead of creating a bad row — both are failures

    score = await sync_to_async(
        SkillScore.objects.filter(user=user, skill=skill, mode='writing').first
    )()
    assert score is not None, (
        "SkillScore must be linked to the Skill FK (pk), "
        "but bug passes string slug 'a1_ser_estar_basic' as the FK column value"
    )
    assert score.score == 2


@pytest.mark.django_db(transaction=True)
async def test_save_skill_updates_skips_unknown_score_label(make_user, make_skill):
    """Unknown score labels should be silently skipped with no DB writes."""
    from engine.onboarding import _save_skill_updates
    from learner.models import SkillScore

    user = await sync_to_async(make_user)(discord_id='u_fk2')
    await sync_to_async(make_skill)(skill_id='a1_ser_estar_basic')

    await _save_skill_updates(user, 'a1_ser_estar_basic:unknown_score')
    count = await sync_to_async(SkillScore.objects.filter(user=user).count)()
    assert count == 0


@pytest.mark.django_db(transaction=True)
async def test_save_skill_updates_none_string_produces_no_writes(make_user):
    """'none' or empty string → no DB writes."""
    from engine.onboarding import _save_skill_updates
    from learner.models import SkillScore

    user = await sync_to_async(make_user)(discord_id='u_fk3')
    await _save_skill_updates(user, 'none')
    await _save_skill_updates(user, '')
    count = await sync_to_async(SkillScore.objects.filter(user=user).count)()
    assert count == 0


@pytest.mark.django_db(transaction=True)
async def test_save_skill_updates_score_map(make_user, make_skill):
    """Score labels map correctly: developing→2, confident→3, mastered→4."""
    from engine.onboarding import _save_skill_updates
    from learner.models import SkillScore

    user = await sync_to_async(make_user)(discord_id='u_fk4')
    skill = await sync_to_async(make_skill)(skill_id='a1_present_regular')

    try:
        await _save_skill_updates(user, 'a1_present_regular:confident')
    except Exception:
        pass

    score = await sync_to_async(
        SkillScore.objects.filter(user=user, skill=skill, mode='writing').first
    )()
    assert score is not None, "Score row must be linked to correct Skill FK"
    assert score.score == 3


# ── Listo flow: quiz persona conflict bug ─────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_quiz_start_does_not_use_persona(make_user):
    """
    BUG (MUST FAIL): When the quiz calls call_llm, it passes user=None,
    which loads the full Luz Angela persona. The persona's instructions
    ("Keep responses short", "No bullet points") conflict with the required
    CONTINUE/SKILL_UPDATES/NEXT_QUESTION structured output format — causing
    NEXT_QUESTION to be missing and the user to see "Can you repeat that?".

    Fix: quiz calls should use a system-override parameter that bypasses the
    persona and uses only the structured quiz prompt.
    """
    from unittest.mock import patch
    from engine.onboarding import handle_onboarding

    user = await sync_to_async(make_user)(
        discord_id='u_listo1', onboarding_complete=False, display_name='Test'
    )

    call_args = []

    async def capture_call_llm(messages, user=None, max_tokens=1024,
                               system_suffix=None, system_override=None):
        call_args.append({'user_arg': user, 'system_override': system_override})
        return (
            "CONTINUE\n"
            "SKILL_UPDATES: none\n"
            "NEXT_QUESTION: What does 'gracias' mean?\n"
            "*(Not sure? Say \"I don't know\" — it's more useful than guessing)*"
        )

    with patch('engine.onboarding.call_llm', capture_call_llm):
        await handle_onboarding(user, 'listo')

    assert call_args, "call_llm was never called for the quiz"
    # BUG: currently system_override is None — Luz Angela persona is used
    # Fix: quiz calls must pass system_override to bypass the persona
    assert call_args[0]['system_override'] is not None, (
        "Quiz LLM calls must pass system_override — without it, the Luz Angela persona "
        "('Keep responses short', 'No bullet points') conflicts with the required "
        "CONTINUE/SKILL_UPDATES/NEXT_QUESTION format and causes NEXT_QUESTION to go missing"
    )
