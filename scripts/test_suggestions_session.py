import requests
from pprint import pprint

BASE = 'http://127.0.0.1:5000'

def main():
    s = requests.Session()
    # Login
    resp = s.post(f'{BASE}/login', data={'email':'anas@gmail','password':'123456'}, allow_redirects=True)
    print('Login status:', resp.status_code)
    # Fetch suggestions page
    r = s.get(f'{BASE}/suggestions')
    print('/suggestions status:', r.status_code)
    # Call API
    payload = {'max_suggestions': 5}
    api = s.post(f'{BASE}/api/suggestions', json=payload)
    print('/api/suggestions status:', api.status_code)
    try:
        pprint(api.json())
    except Exception:
        print('Non-JSON response:')
        print(api.text[:1000])

if __name__ == '__main__':
    main()
