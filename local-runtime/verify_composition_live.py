"""Read-only acceptance of the real UI/Agent composition outputs."""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import av
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
from verify_film import inspect

ROOT=Path(__file__).resolve().parents[1]
CANVAS='izACBM-Sxyckzv5YD5OEH'
CASES={
    'ui':('合成验证｜重排裁剪｜9秒',[('video-1793117533660-i7neh',1,3),('video-1793114908958-rw8tz',2,4),('video-1793117722003-cjjcv',0,2)]),
    'agent':('Agent合成验证｜夜景到晨光｜4秒',[('video-1793117633874-g1xam',1,2),('video-1793117722003-cjjcv',3,2)]),
    'placement':('自动避让验证｜2秒',[('video-1793117722003-cjjcv',0,2)])}

def read(path):
    with urllib.request.urlopen('http://127.0.0.1:3000'+path,timeout=20) as response:return json.load(response)

def main(case):
    title,expected=CASES[case]
    projects=json.loads(read('/api/local-runtime/workspace/state/canvas')['data']['value'])['state']['projects']
    project=next(p for p in projects if p['id']==CANVAS)
    node=next(n for n in project['nodes'] if n['title']==title)
    metadata=node['metadata'];identifier=metadata['compositionTaskId']
    assert metadata['status']=='success',metadata
    assert metadata['compositionOptions']['clips']==[{'nodeId':n,'startSeconds':s,'durationSeconds':d} for n,s,d in expected]
    job=read('/api/local-runtime/v1/compositions/'+identifier)
    assert job['status']=='completed'
    folder=ROOT/'data/local-runtime/compositions'/identifier
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    disk=ROOT/'data/workspace/files'/job['sha256'][:2]/job['sha256']
    assert hashlib.sha256(disk.read_bytes()).hexdigest()==job['sha256']
    assert disk.read_bytes()==(folder/'film.mp4').read_bytes()
    media,still=inspect(disk,take=1)
    duration=sum(d for _,_,d in expected)
    assert media['frames']==duration*24 and media['seconds']==duration and abs(media['audioSeconds']-duration)<.05
    with av.open(str(disk)) as movie:output=[frame.to_ndarray(format='rgb24') for frame in movie.decode(video=0)]
    differences=[];offset=0
    for index,(node_id,start,length) in enumerate(expected):
        original=next(n for n in project['nodes'] if n['id']==node_id)
        assert manifest['clips'][index]['storageKey']==original['metadata']['storageKey']
        source=Path(manifest['clips'][index]['path'])
        with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:3000'+original['metadata']['content'],method='HEAD')) as response:
            assert hashlib.sha256(source.read_bytes()).hexdigest()==response.headers['X-Content-SHA256']
        with av.open(str(source)) as movie:
            target=next(frame.to_ndarray(format='rgb24') for number,frame in enumerate(movie.decode(video=0)) if number==start*24+6)
        error=float(np.abs(output[offset+6].astype(np.float32)-target.astype(np.float32)).mean())
        assert error<6,(index,error)
        differences.append(error);offset+=length*24
    with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:3000'+metadata['content'],headers={'Range':'bytes=-1024'})) as response:
        assert response.status==206 and len(response.read())==1024
    report={'case':case,'canvasId':CANVAS,'nodeId':node['id'],'taskId':identifier,'title':title,'expectedClips':expected,'trimAndOrderPixelMeanErrors':differences,'sourceHashesUnchanged':True,'rangeHttpStatus':206,'sharedDiskPath':str(disk),'media':media,'complete':True}
    destination=ROOT/'data/local-runtime/composition-acceptance'/f'{case}-report.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    still.save(destination.with_suffix('.jpg'),quality=94)
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main(sys.argv[1])
