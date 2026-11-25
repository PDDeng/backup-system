#!/usr/bin/env python
# Python 2.7 / 3; notification errors never change backup result.
from __future__ import print_function
import json, sys, time
try:
    from urllib.request import Request, urlopen
except ImportError:
    from urllib2 import Request, urlopen

def main():
    with open(sys.argv[1], 'rb') as f:
        c = json.loads(f.read().decode('utf-8-sig'))
    if not c.get('Enabled', True):
        return 0
    event = sys.argv[2]
    if event == 'START' and not c.get('NotifyStart', True):
        return 0
    if event == 'SUCCESS' and not c.get('NotifySuccess', True):
        return 0
    message = 'CORE BACKUP | %s | %s | %s' % (event, sys.argv[3], sys.argv[4])
    data = json.dumps({'msgtype':'text','text':{'content':message}}, ensure_ascii=True).encode('utf-8')
    url = c['WebhookUrl'].strip()
    if not url.startswith('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key='):
        raise ValueError('Unexpected webhook endpoint')
    for attempt in range(3):
        try:
            response = urlopen(Request(url, data, {'Content-Type':'application/json'}), timeout=10)
            result = json.loads(response.read().decode('utf-8'))
            if result.get('errcode') == 0:
                return 0
        except Exception:
            pass
        if attempt < 2:
            time.sleep(2)
    print('NOTIFY_FAILED event=%s; check DNS/TLS/network/robot configuration' % event, file=sys.stderr)
    return 1

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('NOTIFY_FAILED configuration or runtime error', file=sys.stderr)
        sys.exit(1)
