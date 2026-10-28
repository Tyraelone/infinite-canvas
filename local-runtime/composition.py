"""Frame-accurate local video composition; its CLI runs only trusted manifests."""
import hashlib
import json
import math
import sys
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from film_edit import add_titles, score_block

FPS, RATE = 24, 48000


def open_source(path):
    try:
        return av.open(str(path),options={'format_whitelist':'mov,matroska,webm,avi,mpegts','protocol_whitelist':'file'})
    except av.error.FFmpegError as error:
        raise ValueError('视频格式不支持或文件损坏；请使用MP4/MOV、WebM/MKV、AVI或TS') from error


def number(value, label, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < (0 if zero else 1/FPS):
        raise ValueError(label+'必须是有效秒数')
    return round(value*FPS)/FPS


def normalize_request(body):
    if not isinstance(body, dict) or set(body)-{'requestId','title','clips','keepAudio','openingText','endingText','score'}:
        raise ValueError('合成请求参数无效')
    clips = body.get('clips')
    if not isinstance(clips, list) or not 1 <= len(clips) <= 50:
        raise ValueError('请选择1至50段视频')
    normalized=[]
    for item in clips:
        if not isinstance(item, dict) or set(item)-{'storageKey','startSeconds','durationSeconds'}:
            raise ValueError('合成来源必须使用共享媒体键，不能使用路径或URL')
        key=item.get('storageKey')
        if not isinstance(key,str) or not key or len(key)>512 or any(char in key for char in '/\\\x00') or ':' not in key:
            raise ValueError('共享媒体键无效')
        normalized.append({'storageKey':key,'startSeconds':number(item.get('startSeconds',0),'起点',zero=True),
                           'durationSeconds':number(item.get('durationSeconds'),'使用时长')})
    if sum(item['durationSeconds'] for item in normalized)>1800:
        raise ValueError('合成总时长不能超过1800秒')
    result={'clips':normalized}
    for key,default in [('title','合成视频'),('openingText',''),('endingText','')]:
        value=body.get(key,default)
        if not isinstance(value,str) or len(value)>(120 if key=='title' else 60):
            raise ValueError('合成标题或片头片尾文字过长')
        result[key]=value.strip()
    for key,default in [('keepAudio',True),('score',False)]:
        if not isinstance(body.get(key,default),bool):raise ValueError(key+'必须是布尔值')
        result[key]=body.get(key,default)
    return result


def probe(path):
    with open_source(path) as source:
        if not source.streams.video:raise ValueError('来源不是有效视频')
        stream=source.streams.video[0]
        duration=float(stream.duration*stream.time_base) if stream.duration is not None else float(source.duration or 0)/av.time_base
        if not 0<duration or not 16<=stream.width<=3840 or not 16<=stream.height<=3840 or stream.width*stream.height>8_294_400:
            raise ValueError('视频时长或画幅无效；最大支持3840边长、829万像素')
        return {'width':stream.width,'height':stream.height,'durationSeconds':duration,'frames':stream.frames}


def frames(path, start, duration):
    with open_source(path) as source:
        graph=av.filter.Graph()
        last=graph.add_buffer(template=source.streams.video[0])
        for name,args in [('setpts','PTS-STARTPTS'),('trim',f'start={start}:duration={duration}'),('setpts','PTS-STARTPTS'),('fps','fps=24:round=near')]:
            node=graph.add(name,args);last.link_to(node);last=node
        sink=graph.add('buffersink');last.link_to(sink);graph.configure()
        for frame in source.decode(video=0):
            graph.push(frame)
            while True:
                try:yield sink.pull()
                except (av.error.BlockingIOError,av.error.EOFError):break
        graph.push(None)
        while True:
            try:yield sink.pull()
            except (av.error.BlockingIOError,av.error.EOFError):break


def sound(path, start, count, enabled):
    values=np.zeros((2,count),dtype=np.float32)
    if not enabled:return values
    with open_source(path) as source:
        if not source.streams.audio:return values
        video=source.streams.video[0]
        origin=float((video.start_time or 0)*video.time_base)
        left=round((origin+start)*RATE);right=left+count
        resampler=av.AudioResampler(format='fltp',layout='stereo',rate=RATE)
        cursor=0
        for frame in source.decode(audio=0):
            for block in resampler.resample(frame):
                begin=round(float(block.pts*block.time_base)*RATE) if block.pts is not None else cursor
                end=begin+block.samples;cursor=end
                a,b=max(begin,left),min(end,right)
                if b>a:values[:,a-left:b-left]=block.to_ndarray()[:,a-begin:b-begin]
            if cursor>=right:break
    if not np.isfinite(values).all():raise ValueError('音轨包含无效采样')
    peak=float(np.max(np.abs(values)))
    if peak:values*=min(1,.65/peak)
    ramp=min(3840,count//4)
    values[:,:ramp]*=np.linspace(0,1,ramp);values[:,-ramp:]*=np.linspace(1,0,ramp)
    return values


def render_composition(manifest, progress=None):
    clips=manifest['clips'];destination=Path(manifest['output'])
    if destination.exists():raise FileExistsError(destination)
    sources=[probe(item['path']) for item in clips]
    for item,metadata in zip(clips,sources):
        start=number(item.get('startSeconds',0),'起点',zero=True);duration=number(item['durationSeconds'],'使用时长')
        if start+duration>metadata['durationSeconds']+1e-4:raise ValueError('裁剪范围超过原视频时长')
    width=sources[0]['width']//2*2;height=sources[0]['height']//2*2
    total=sum(round(item['durationSeconds']*FPS) for item in clips);index=0
    temporary=destination.with_suffix('.partial.mp4');destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        with av.open(str(temporary),'w',options={'movflags':'+faststart'}) as output:
            video=output.add_stream('libx264',rate=FPS);video.width,video.height,video.pix_fmt=width,height,'yuv420p'
            video.options={'crf':'18','preset':'medium'}
            audio=output.add_stream('aac',rate=RATE);audio.layout,audio.bit_rate='stereo',192000
            for clip in clips:
                count=round(clip['durationSeconds']*FPS)
                samples=sound(clip['path'],clip.get('startSeconds',0),count*2000,manifest.get('keepAudio',True))
                decoded=frames(clip['path'],clip.get('startSeconds',0),count/FPS)
                try:
                    for local in range(count):
                        frame=next(decoded,None)
                        if frame is None:raise ValueError('视频有效帧不足，不能冻结或循环补时长')
                        scale=min(width/frame.width,height/frame.height)
                        w,h=max(1,round(frame.width*scale)),max(1,round(frame.height*scale))
                        pixels=np.zeros((height,width,3),dtype=np.uint8)
                        pixels[(height-h)//2:(height-h)//2+h,(width-w)//2:(width-w)//2+w]=frame.reformat(width=w,height=h,format='rgb24').to_ndarray()
                        pixels=add_titles(pixels,index,total,manifest.get('openingText',''),manifest.get('endingText',''))
                        result=av.VideoFrame.from_ndarray(pixels,format='rgb24');result.pts,result.time_base=index,Fraction(1,FPS)
                        for packet in video.encode(result):output.mux(packet)
                        block=samples[:,local*2000:(local+1)*2000].copy()
                        if manifest.get('score'):block+=score_block(index*2000,2000,total*2000)
                        sound_frame=av.AudioFrame.from_ndarray(np.ascontiguousarray(np.clip(block,-.98,.98)),format='fltp',layout='stereo')
                        sound_frame.sample_rate,sound_frame.pts,sound_frame.time_base=RATE,index*2000,Fraction(1,RATE)
                        for packet in audio.encode(sound_frame):output.mux(packet)
                        index+=1
                        if progress and (index%24==0 or index==total):progress({'frames':index,'totalFrames':total,'progress':round(index/total*95,1)})
                finally:decoded.close()
            for stream in (video,audio):
                for packet in stream.encode():output.mux(packet)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {'width':width,'height':height,'frames':total,'fps':FPS,'durationSeconds':total/FPS,
            'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__=='__main__':
    manifest=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    if manifest.get('parentPid'):
        import os
        import threading
        import psutil
        parent=psutil.Process(manifest['parentPid']);created=parent.create_time()
        def watch_parent():
            while True:
                threading.Event().wait(1)
                try:
                    if not parent.is_running() or parent.create_time()!=created:os._exit(2)
                except psutil.NoSuchProcess:os._exit(2)
        threading.Thread(target=watch_parent,daemon=True).start()
    print(json.dumps({'result':render_composition(manifest,lambda value:print(json.dumps(value),flush=True))}),flush=True)
