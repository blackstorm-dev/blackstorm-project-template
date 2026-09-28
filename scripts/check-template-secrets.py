# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Validate staged secrets; official templates contain examples only."""
import argparse
import configparser
import json
import os
from pathlib import PurePosixPath
import re
import subprocess
from urllib.parse import urlsplit

import yaml

OFFICIAL = {'blackstorm-dev/blackstorm-template', 'blackstorm-dev/blackstorm-project-template'}


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.DEVNULL).decode()


def repository():
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return os.environ.get('GITHUB_REPOSITORY', '').lower()
    try:
        remote = git('remote', 'get-url', 'origin').strip()
    except subprocess.CalledProcessError:
        return ''
    if remote.startswith('git@github.com:'):
        path = remote.split(':', 1)[1]
    else:
        url = urlsplit(remote)
        if url.hostname != 'github.com':
            return ''
        path = url.path.lstrip('/')
    return path.rstrip('/').removesuffix('.git').lower()


def secret_path(path):
    return bool(re.match(r'^(secrets/|(?:live|deploy)/[^/]+/secrets/)', path))


def sensitive_value(key, value):
    if value == 'REPLACE_ME':
        return True
    if key == 'type':
        return value == 'git'
    if key == 'repoURLIsRegex':
        return value == 'true'
    if key == 'url':
        return value == 'https://github.com/example-org'
    if key == '.dockerconfigjson':
        data = json.loads(value)
        auths = data['auths']
        return (set(data) == {'auths'} and set(auths) == {'https://index.docker.io/v1/'}
                and bool(auths['https://index.docker.io/v1/'])
                and all(v == 'REPLACE_ME' for v in auths['https://index.docker.io/v1/'].values()))
    if key == 'cloud':
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(value)
        return (config.sections() == ['default'] and not config.defaults()
                and set(config['default']) == {'aws_access_key_id', 'aws_secret_access_key'}
                and all(v == 'REPLACE_ME' for v in config['default'].values()))
    return False


def example_valid(path, text):
    target = path.removesuffix('.example')
    header = [
        '# Run make init once to configure your SOPS key.',
        f'# Run make secrets FILE={target} to create and edit the encrypted file.',
        '# Replace placeholders in the editor; commit the encrypted file, never plaintext.',
    ]
    if text.splitlines()[:3] != header:
        return False
    if target.endswith('.env'):
        entries = [line for line in text.splitlines() if line.strip() and not line.startswith('#')]
        return bool(entries) and all(re.fullmatch(r'[A-Z][A-Z0-9_]*=REPLACE_ME', line) for line in entries)
    if not target.endswith(('.yaml', '.yml')):
        return False
    document = yaml.safe_load(text)
    if not isinstance(document, dict) or 'sops' in document:
        return False
    if document.get('kind') == 'SopsSecret':
        secrets = document.get('spec', {}).get('secretTemplates', [])
    elif document.get('kind') == 'Secret':
        secrets = [document]
    else:
        return False
    if not isinstance(secrets, list) or not secrets:
        return False
    for secret in secrets:
        fields = [secret[k] for k in ('data', 'stringData') if k in secret]
        if not fields or any(not isinstance(f, dict) or not f for f in fields):
            return False
        if not all(sensitive_value(k, v) for f in fields for k, v in f.items()):
            return False
    return True


def validate(path, text, official):
    if path.endswith('.example'):
        if not example_valid(path, text):
            return 'example must keep its setup header and placeholder secret values'
    elif official:
        # Resource lists are needed to render the examples, but may not embed generators or patches.
        if PurePosixPath(path).name == 'kustomization.yaml':
            document = yaml.safe_load(text)
            if (isinstance(document, dict) and document.get('kind') == 'Kustomization'
                    and set(document) <= {'apiVersion', 'kind', 'resources'}):
                return None
        return 'official templates allow only .example secrets and resource-only kustomization.yaml files'
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='Check all tracked files instead of staged changes')
    args = parser.parse_args()
    os.chdir(git('rev-parse', '--show-toplevel').strip())
    paths = (git('ls-files', '-z') if args.all else
             git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z')).split('\0')
    official = repository() in OFFICIAL
    failed = False
    for path in filter(secret_path, paths):
        try:
            message = validate(path, git('show', ':' + path), official)
        except (ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError, configparser.Error):
            message = 'invalid example or secret manifest'
        if message:
            # Never print a secret value or a parser exception containing file contents.
            print(f'{path}: {message}')
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
