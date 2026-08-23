import pytest


@pytest.fixture
def make_user():
    """Sync factory for User model instances. Use within @pytest.mark.django_db tests."""
    from learner.models import User
    counter = [0]

    def factory(discord_id=None, display_name='Test', cefr_level='A2', onboarding_complete=True):
        counter[0] += 1
        did = discord_id or str(1000 + counter[0])
        return User.objects.create(
            discord_id=did,
            display_name=display_name,
            estimated_cefr_level=cefr_level,
            onboarding_complete=onboarding_complete,
        )
    return factory


@pytest.fixture
def make_skill():
    """Sync factory for Skill model instances. Use within @pytest.mark.django_db tests."""
    from learner.models import Skill
    counter = [0]

    def factory(skill_id=None, name=None, cefr_level='A1', order=None,
                description='Test skill.', active=True):
        counter[0] += 1
        sid = skill_id or f'test_skill_{counter[0]}'
        sname = name or sid.replace('_', ' ').title()
        sorder = order if order is not None else counter[0]
        return Skill.objects.create(
            skill_id=sid, name=sname, cefr_level=cefr_level,
            order=sorder, description=description, active=active,
        )
    return factory


@pytest.fixture
def engine_caplog(caplog):
    """caplog wired to the `engine` logger tree.

    settings.LOGGING gives `engine` its own console handler with propagate=False,
    so its records never reach caplog's root handler on their own.
    """
    import logging
    logger = logging.getLogger('engine')
    bot_logger = logging.getLogger('bot')
    logger.addHandler(caplog.handler)
    bot_logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        bot_logger.removeHandler(caplog.handler)
