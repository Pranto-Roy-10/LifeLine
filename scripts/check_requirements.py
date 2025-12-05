import sys
try:
    import pkg_resources
    def get_installed():
        return {pkg.key for pkg in pkg_resources.working_set}
except Exception:
    # fallback to importlib.metadata (py3.8+)
    try:
        from importlib.metadata import distributions
    except Exception:
        from importlib_metadata import distributions  # type: ignore

    def get_installed():
        names = set()
        for dist in distributions():
            try:
                names.add(dist.metadata['Name'].lower())
            except Exception:
                try:
                    names.add(dist.metadata.get('Name','').lower())
                except Exception:
                    pass
        return names
from pathlib import Path

req_path = Path(__file__).parents[1] / 'requirements.txt'
if not req_path.exists():
    print('ERROR: requirements.txt not found at', req_path)
    sys.exit(2)

raw = [l.strip() for l in req_path.read_text(encoding='utf-8-sig', errors='replace').splitlines()]
reqs = []
for line in raw:
    if not line or line.startswith('#'):
        continue
    # support lines like 'package==version' or comments
    reqs.append(line)

installed = get_installed()

missing = []
for r in reqs:
    # extract package name before comparison operators
    name = r.split('==')[0].split('>=')[0].split('>')[0].split('<')[0].strip()
    if name.lower() not in installed:
        missing.append((name, r))

print('Found', len(reqs), 'requirements (non-comment).')
print('Detected', len(installed), 'installed packages in current environment.')
print('')
if not missing:
    print('All required packages appear to be installed.')
else:
    print('Missing packages:')
    for name, line in missing:
        print(' -', line)
    print('\nTo install missing packages run:')
    pkgs = ' '.join([line for _, line in missing])
    print('  pip install ' + pkgs)

# also show quick verification for key imports
checks = {
    'flask_socketio': 'from flask_socketio import SocketIO',
    'googletrans': 'from googletrans import Translator',
}
print('\nQuick import checks:')
for key, stmt in checks.items():
    try:
        exec(stmt, {})
        print(f' - {key}: OK')
    except Exception as e:
        print(f' - {key}: FAIL ({e})')
