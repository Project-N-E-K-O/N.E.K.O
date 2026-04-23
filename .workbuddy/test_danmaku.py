import requests, json, time, sys

resp = requests.post('http://localhost:48916/runs', json={
    'plugin_id': 'bilibili_danmaku',
    'entry_id': 'get_danmaku',
    'args': {'max_count': 5, 'include_gifts': True}
})
data = resp.json()
run_id = data.get('run_id') or data.get('id')
print(f'Run ID: {run_id}')

for i in range(5):
    time.sleep(1)
    poll = requests.get(f'http://localhost:48916/runs/{run_id}')
    status = poll.json().get('status')
    print(f'Poll {i+1}: status={status}')
    if status == 'succeeded':
        exp = requests.get(f'http://localhost:48916/runs/{run_id}/export')
        result = exp.json()
        print(f'danmaku_count: {result.get("danmaku_count", 0)}')
        print(f'sc_count: {result.get("sc_count", 0)}')
        print(f'gift_count: {result.get("gift_count", 0)}')
        danmaku = result.get('danmaku', [])
        if danmaku:
            for d in danmaku[:3]:
                print(f'  [{d.get("user_name")}]: {d.get("content")}')
        else:
            print('  (no danmaku)')
        break
    elif status in ('failed', 'error'):
        print(f'Error: {json.dumps(poll.json(), ensure_ascii=False)}')
        break
