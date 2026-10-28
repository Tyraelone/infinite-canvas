"""Collect only the real completed studio jobs created by the canvas Agent."""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_film import inspect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('production', type=Path)
    args = parser.parse_args()
    production = json.loads(args.production.read_text(encoding='utf-8-sig'))
    root = args.production.resolve().parent
    folder = root/'shots'
    folder.mkdir(exist_ok=True)
    records = {}
    for number, job_id in sorted(production.get('clipJobs', {}).items()):
        with urllib.request.urlopen('http://127.0.0.1:8190/api/jobs/'+job_id, timeout=30) as response:
            job = json.load(response)
        records[number] = {'taskId': job_id, 'status': job['status'], 'stage': job.get('stage'), 'elapsed': job.get('elapsed')}
        if job['status'] != 'completed':
            continue
        assert job['spec']['duration'] == 15 and job['spec']['audio'] is True
        path = folder/f'{number}-{job_id}.mp4'
        if not path.exists():
            temporary = path.with_suffix('.download')
            with urllib.request.urlopen('http://127.0.0.1:3000/api/local-runtime/media/video/'+job_id, timeout=120) as response, temporary.open('wb') as output:
                assert response.status == 200 and 'video/' in response.headers.get('Content-Type', '')
                while chunk := response.read(1024*1024):
                    output.write(chunk)
            temporary.replace(path)
        media, still = inspect(path)
        assert media['frames'] >= 360 and media['fps'] == 24 and media['seconds'] >= 15
        records[number].update(media)
        still.save(folder/f'{number}-preview.jpg', quality=95)
    (root/'shots.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    completed = [record for record in records.values() if record['status'] == 'completed']
    if len(completed) == 12:
        manifest = {'canvasId': production['canvasId'], 'title': production['title'], 'clips': [records[f'{number:02d}']['path'] for number in range(1, 13)],
                    'output': str(root/'纸翼归航_180s.mp4'), 'secondsEach': 15, 'score': production.get('score', False)}
        (root/'edit.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'completed': len(completed), 'known': len(records), 'shots': {key: {field: value for field, value in record.items() if field in ('status', 'frames', 'seconds', 'audioPeak')} for key, record in records.items()}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
