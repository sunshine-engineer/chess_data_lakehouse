"""Local app accounts; passwords are never sent to Lichess."""
from datetime import datetime,timedelta,timezone
import hashlib
import hmac
import re
import secrets
from uuid import uuid4

from werkzeug.security import generate_password_hash,check_password_hash

from . import storage as db

COOKIE='chess_session'


def credentials(username,password):
    """Validate local account names and password length before authentication changes."""
    if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}',username.strip()):
        raise ValueError('Account name must contain 3–40 letters, numbers, dots, underscores or hyphens')
    if not isinstance(password,str) or not 10<=len(password)<=128:
        raise ValueError('Use a password of 10–128 characters for this app')
    return username.strip()


def create(username,password):
    """Create a local study account with a password hash rather than plaintext credentials."""
    name=credentials(username,password)
    with db.connect() as c:
        owner=str(uuid4())
        row=c.execute('INSERT INTO study.accounts(id,username,username_key,password_hash) VALUES(%s,%s,%s,%s) ON CONFLICT(username_key) DO NOTHING RETURNING id,username',
            (owner,name,name.casefold(),generate_password_hash(password,method='scrypt'))).fetchone()
        if not row:raise ValueError('This account name is already taken')
        return row


def login(username,password):
    """Verify local account credentials and return the authenticated account record."""
    name=credentials(username,password)
    key=hashlib.sha256(name.casefold().encode()).hexdigest()
    with db.connect() as c:
        c.execute("INSERT INTO study.auth_attempts(key) VALUES(%s) ON CONFLICT DO NOTHING",(key,))
        attempt=c.execute('SELECT * FROM study.auth_attempts WHERE key=%s FOR UPDATE',(key,)).fetchone()
        if attempt['blocked_until'] and attempt['blocked_until']>datetime.now(timezone.utc):
            raise ValueError('Too many sign-in attempts. Try again in a few minutes.')
        row=c.execute('SELECT * FROM study.accounts WHERE username_key=%s',(name.casefold(),)).fetchone()
        okay=row and check_password_hash(row['password_hash'],password)
        if not okay:
            c.execute("""UPDATE study.auth_attempts SET failures=CASE WHEN window_start<now()-interval '10 minutes' THEN 1 ELSE failures+1 END,
                window_start=CASE WHEN window_start<now()-interval '10 minutes' THEN now() ELSE window_start END,
                blocked_until=CASE WHEN failures>=9 AND window_start>=now()-interval '10 minutes' THEN now()+interval '10 minutes' ELSE NULL END WHERE key=%s""",(key,))
            c.commit()
            raise ValueError('Account name or password is incorrect')
        c.execute('DELETE FROM study.auth_attempts WHERE key=%s',(key,))
        return {'id':str(row['id']),'username':row['username']}


def new_session(owner):
    """Create a private browser session and its separate CSRF value."""
    token=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32)
    with db.connect() as c:
        c.execute('DELETE FROM study.sessions WHERE expires_at<now()')
        c.execute('INSERT INTO study.sessions(token_hash,owner_id,csrf,expires_at) VALUES(%s,%s,%s,%s)',
            (hashlib.sha256(token.encode()).hexdigest(),owner,csrf,datetime.now(timezone.utc)+timedelta(days=14)))
    return token,csrf


def session(token):
    """Resolve a valid browser session without exposing the stored password hash."""
    if not token or len(token)>200:return None
    with db.connect() as c:
        row=c.execute('''SELECT a.id,a.username,s.csrf FROM study.sessions s JOIN study.accounts a ON a.id=s.owner_id
            WHERE s.token_hash=%s AND s.expires_at>now()''',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return dict(row,id=str(row['id'])) if row else None


def logout(token):
    """Revoke only the supplied local browser session token."""
    with db.connect() as c:c.execute('DELETE FROM study.sessions WHERE token_hash=%s',(hashlib.sha256(token.encode()).hexdigest(),))


def csrf_matches(user,value):
    """Compare the submitted CSRF value with the authenticated session's expected value."""
    return bool(user and isinstance(value,str) and hmac.compare_digest(user['csrf'],value))
