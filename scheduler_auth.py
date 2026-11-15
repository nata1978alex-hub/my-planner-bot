"""Validate short-lived tokens issued to the reminder workflow."""
import jwt

ISSUER = "https://token.actions.githubusercontent.com"
WORKFLOW = "nata1978alex-hub/my-planner-bot/.github/workflows/reminders.yml@refs/heads/main"
KEYS = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks", timeout=5)


def is_scheduler(authorization, audience):
    scheme, _, token = authorization.partition(" ")
    if scheme != "Bearer" or not token or len(token) > 8192:
        return False
    try:
        key = KEYS.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=ISSUER,
                            audience=audience, options={"require": ["exp", "iat", "nbf", "aud", "iss"]})
    except jwt.PyJWTError:
        return False
    return (claims.get("repository_id") == "1458620691"
            and claims.get("ref") == "refs/heads/main"
            and claims.get("workflow_ref") == WORKFLOW
            and claims.get("event_name") in {"schedule", "workflow_dispatch", "push"})
