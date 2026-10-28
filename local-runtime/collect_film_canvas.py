"""Save the canvas Agent's real storyboard, frame IDs and video job IDs locally."""
import argparse
import base64
import hashlib
import json
import re
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('production', type=Path)
    args = parser.parse_args()
    production = json.loads(args.production.read_text(encoding='utf-8-sig'))
    root = args.production.resolve().parent
    (root/'frames').mkdir(exist_ok=True)
    with urllib.request.urlopen('http://127.0.0.1:3000/api/local-runtime/workspace/state/canvas', timeout=60) as response:
        state = json.load(response)['data']
    project = next(item for item in json.loads(state['value'])['state']['projects'] if item['id'] == production['canvasId'])
    records = []
    for node in project['nodes']:
        metadata = node.get('metadata', {})
        number = re.match(r'^(\d{2})[｜|]', node.get('title', ''))
        if node['type'] == 'text':
            filename = 'storyboard-current.md' if node['id'] == production['primaryScriptNode'] else 'note-'+node['id']+'.md'
            (root/filename).write_text(metadata.get('content', ''), encoding='utf-8')
        if node['type'] == 'video' and number and re.fullmatch('[a-f0-9]{32}', metadata.get('videoTaskId', '')):
            production.setdefault('clipJobs', {})[number[1]] = metadata['videoTaskId']
        if node['type'] == 'image' and metadata.get('status') == 'success':
            key = number[1] if number else '00' if node['id'] == production['primaryReferenceNode'] else None
            if key is None:
                continue
            url = metadata['content']
            if url.startswith('data:image/'):
                raw = base64.b64decode(url.split(',', 1)[1], validate=True)
            elif url.startswith('/api/local-runtime/'):
                with urllib.request.urlopen('http://127.0.0.1:3000'+url, timeout=60) as response:
                    raw = response.read()
            else:
                raise ValueError('Unexpected frame URL: '+url[:80])
            path = root/'frames'/f'{key}.png'
            path.write_bytes(raw)
            records.append({'number': key, 'nodeId': node['id'], 'title': node['title'], 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})
    production['frames'] = records
    production['lastCanvasRevision'] = state['revision']
    production['canvasNodeCount'] = len(project['nodes'])
    args.production.write_text(json.dumps(production, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'revision': state['revision'], 'nodes': len(project['nodes']), 'frames': {item['number']: item['nodeId'] for item in records}, 'clipJobs': production.get('clipJobs', {})}, ensure_ascii=False))


if __name__ == '__main__':
    main()
