from playwright.sync_api import sync_playwright
import json
import time
import requests

BASE = 'http://127.0.0.1:5000'
EMAIL = 'anas@gmail'
PASSWORD = '123456'

def run():
    logs = []
    network = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        # capture console messages
        def on_console(msg):
            logs.append({'type': 'console', 'text': msg.text, 'location': msg.location})

        # capture responses
        def on_response(resp):
            try:
                url = resp.url
                status = resp.status
                if '/api/suggestions' in url:
                    try:
                        body = resp.text()
                    except Exception:
                        body = '<non-text body>'
                    network.append({'url': url, 'status': status, 'body': body})
                else:
                    network.append({'url': url, 'status': status})
            except Exception as e:
                network.append({'error': str(e)})


        # Programmatic login using requests to obtain session cookie, then set it in Playwright
        s = requests.Session()
        login_resp = s.post(f'{BASE}/login', data={'email': EMAIL, 'password': PASSWORD}, allow_redirects=True)
        # create page after setting cookies
        page = context.new_page()
        page.on('console', lambda msg: on_console(msg))
        page.on('response', lambda resp: on_response(resp))

        # Transfer cookies from requests session to Playwright context
        for c in s.cookies:
            try:
                context.add_cookies([{
                    'name': c.name,
                    'value': c.value,
                    'domain': '127.0.0.1',
                    'path': c.path or '/',
                }])
            except Exception:
                pass


        # Go to suggestions
        page.goto(f'{BASE}/suggestions')
        try:
            page.wait_for_selector('#smart-suggestions-widget', timeout=15000)
        except Exception:
            print('Warning: suggestions widget selector not found within timeout')

        # Wait for initial load then click Refresh
        time.sleep(1)
        # click refresh
        page.click('#refresh-suggestions')

        # wait a bit for network
        page.wait_for_timeout(3000)

        # collect inner HTML of suggestions container
        try:
            content = page.eval_on_selector('#suggestions-container', 'el => el.innerHTML')
        except Exception:
            content = ''

        out = {
            'console': logs,
            'network': network,
            'suggestions_html_snapshot': content,
        }

        with open('playwright_suggestions_log.json', 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        print('Saved log to playwright_suggestions_log.json')

        context.close()
        browser.close()

if __name__ == '__main__':
    run()
