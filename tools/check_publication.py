"""Guard public Git metadata and paths; not a source-content secret scanner."""
import argparse
from pathlib import PurePosixPath
import subprocess
import sys


def git(*args):
    return subprocess.check_output(['git', *args], text=True, encoding='utf-8').strip()


def private_path(name):
    p = PurePosixPath(name.lower())
    return (any(part in {'.private', 'data', '__pycache__', '.venv'} for part in p.parts)
            or p.name in {'config.json', 'control.json', 'auth.json', 'admin-access.json',
                          'ssh-credentials.json', '.env', 'id_rsa', 'id_ed25519'}
            or (p.name.startswith('.env.') and p.name not in {'.env.example', '.env.sample'})
            or p.name.startswith('control-worker-state')
            or p.name.startswith('control-diagnostics.jsonl')
            or p.suffix in {'.bundle', '.pem', '.key', '.log', '.machine-id'})


def noreply(email):
    local, sep, domain = email.strip().rpartition('@')
    return bool(local and sep and domain == 'users.noreply.github.com')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    errors = []
    paths = git('ls-files', '-z').split('\0')
    if args.staged:
        # var reflects GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL overrides as well.
        for role in ('AUTHOR', 'COMMITTER'):
            ident = git('var', 'GIT_' + role + '_IDENT')
            email = ident.rsplit('<', 1)[-1].split('>', 1)[0]
            if not noreply(email):
                errors.append(f'{role.lower()} must use a GitHub noreply email')
    else:
        rows = git('log', '--all', '--format=%H %ae %ce').splitlines()
        for row in rows:
            sha, author, committer = row.split()
            if not noreply(author) or not noreply(committer):
                errors.append(f'commit {sha[:12]} has non-noreply email metadata')
    for name in paths:
        if name and private_path(name):
            errors.append(f'private/runtime file must not be tracked: {name}')
    if errors:
        print('Publication guard failed:\n- ' + '\n- '.join(errors), file=sys.stderr)
        return 1
    print('Publication guard passed (paths and email metadata; content review still required).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
