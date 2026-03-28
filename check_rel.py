import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def check_releases(repo):
    url = f"https://api.github.com/repos/{repo}/releases"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "python"})
        resp = urllib.request.urlopen(req, context=ctx, timeout=10)
        data = json.loads(resp.read())
        if not data:
            print(f"- {repo}: 릴리즈 없음")
        else:
            print(f"- {repo} 최근 릴리즈:")
            for r in data[:2]:
                assets = [a['name'] for a in r.get('assets', [])]
                print(f"  [{r['tag_name']}] 에셋: {assets}")
    except urllib.error.HTTPError as e:
        print(f"- {repo}: 접근 불가 또는 없음 ({e.code})")
    except Exception as e:
        print(f"- {repo}: 에러 ({e})")

print("=== Github Releases 점검 ===")
check_releases("hoonkim1092-web/agent-factory")
check_releases("hoonkim1092-web/af-fsa")
