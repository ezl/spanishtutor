from django.contrib import admin
from .models import User, SkillScore, Session, SessionEvent, EvaluationProgress, QuizQuestion, SessionFeedback


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'discord_id', 'estimated_cefr_level', 'onboarding_complete', 'created_at')
    list_filter = ('estimated_cefr_level', 'onboarding_complete')
    search_fields = ('display_name', 'discord_id')


@admin.register(SkillScore)
class SkillScoreAdmin(admin.ModelAdmin):
    list_display = ('user', 'skill_id', 'mode', 'score', 'last_tested_at', 'next_review_at')
    list_filter = ('mode', 'score')
    search_fields = ('user__display_name', 'skill_id')


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'session_type', 'started_at', 'ended_at')
    list_filter = ('session_type',)


@admin.register(SessionEvent)
class SessionEventAdmin(admin.ModelAdmin):
    list_display = ('session', 'event_type', 'dimension', 'skill_id', 'error_logged', 'timestamp')
    list_filter = ('event_type', 'dimension', 'error_logged')


@admin.register(EvaluationProgress)
class EvaluationProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'phase', 'completed_at')


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ('skill', 'format', 'question_preview', 'active', 'created_at')
    list_filter = ('format', 'active', 'skill__cefr_level')
    search_fields = ('question_text',)
    list_editable = ('active',)

    @admin.display(description='Question')
    def question_preview(self, obj):
        return obj.question_text[:80]


@admin.register(SessionFeedback)
class SessionFeedbackAdmin(admin.ModelAdmin):
    list_display = ('session', 'interpretation_preview', 'resolved', 'created_at')
    list_filter = ('resolved', 'created_at')
    search_fields = ('user_message', 'interpretation', 'resolution_note')
    list_editable = ('resolved',)

    @admin.display(description='Interpretation')
    def interpretation_preview(self, obj):
        return obj.interpretation[:80]
