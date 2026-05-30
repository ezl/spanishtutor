from django.core import signing

_SALT = 'progress-link'
_MAX_AGE = 3600  # 1 hour


def make_progress_token(user_pk: int) -> str:
    return signing.dumps({'user_id': user_pk}, salt=_SALT)


def validate_progress_token(token: str) -> int | None:
    try:
        data = signing.loads(token, salt=_SALT, max_age=_MAX_AGE)
        return data['user_id']
    except (signing.SignatureExpired, signing.BadSignature):
        return None
