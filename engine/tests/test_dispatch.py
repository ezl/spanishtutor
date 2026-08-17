"""Tests for the transport-agnostic dispatch layer."""


class TestDataclasses:
    def test_incoming_event_has_expected_fields(self):
        from engine.dispatch import IncomingEvent
        e = IncomingEvent(
            platform='discord', external_id='u123',
            display_name='Alice', text='hola',
        )
        assert e.platform == 'discord'
        assert e.external_id == 'u123'
        assert e.display_name == 'Alice'
        assert e.text == 'hola'

    def test_incoming_event_has_no_attachments_field(self):
        """Text-only by design (voice lives on web app, not chat platforms).
        No attachments field until we intentionally add multi-modal support."""
        from engine.dispatch import IncomingEvent
        e = IncomingEvent(platform='p', external_id='x', display_name='n', text='t')
        assert not hasattr(e, 'attachments')

    def test_reply_defaults(self):
        from engine.dispatch import Reply
        r = Reply(text='hi')
        assert r.text == 'hi'
        assert r.follow_up is None
        assert r.session_ended is False

    def test_reply_with_all_fields(self):
        from engine.dispatch import Reply
        r = Reply(text='hi', follow_up='and again', session_ended=True)
        assert r.text == 'hi'
        assert r.follow_up == 'and again'
        assert r.session_ended is True
