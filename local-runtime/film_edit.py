"""Assemble real generated shots into a frame-exact local MP4 with PyAV/libav."""
import argparse
import json
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FPS, SAMPLE_RATE = 24, 48000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS


def read_audio(path, samples):
    blocks = []
    with av.open(str(path)) as source:
        if not source.streams.audio:
            raise ValueError(f'镜头缺少音轨：{path}')
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=SAMPLE_RATE)
        for frame in source.decode(audio=0):
            blocks.extend(item.to_ndarray() for item in resampler.resample(frame))
        blocks.extend(item.to_ndarray() for item in resampler.resample(None))
    sound = np.concatenate(blocks, axis=1) if blocks else np.empty((2, 0), dtype=np.float32)
    if sound.shape[1] < samples - 2048:
        raise ValueError(f'镜头音轨不足：{path}')
    sound = np.pad(sound[:, :samples], ((0, 0), (0, max(0, samples-sound.shape[1]))))
    peak = float(np.max(np.abs(sound)))
    if not np.isfinite(peak) or peak <= 0:
        raise ValueError(f'镜头没有有效声音：{path}')
    sound *= min(1, .65 / peak)
    ramp = min(3840, samples//4)
    sound[:, :ramp] *= np.linspace(0, 1, ramp)
    sound[:, -ramp:] *= np.linspace(1, 0, ramp)
    return sound


def score_block(start, count, total):
    """An original sparse glass-tone score, computed from musical notes locally."""
    time = np.arange(start, start+count, dtype=np.float64) / SAMPLE_RATE
    chords = [(50, 57, 65), (46, 53, 62), (53, 60, 69), (48, 55, 64)] if start < total*.7 else [(53, 57, 60), (48, 55, 64), (46, 53, 62), (53, 60, 65)]
    chord = chords[int(time[0] // 8) % len(chords)]
    local = time % 8
    pad = sum(np.sin(2*np.pi*(440*2**((note-69)/12))*time) for note in chord) * .009 * np.sin(np.pi*local/8)**2
    notes = (74, 69, 77, 76, 72, 69, 65, 69) if start < total*.7 else (77, 81, 79, 77, 74, 72, 69, 77)
    note = np.asarray(notes)[(time//2).astype(int) % len(notes)]
    frequency = 440 * 2**((note-69)/12)
    phase = time % 2
    bell = (np.sin(2*np.pi*frequency*phase) + .25*np.sin(4*np.pi*frequency*phase)) * .025 * np.exp(-phase*2.8) * np.minimum(1, phase/.02)
    fade = np.minimum(1, time/2) * np.minimum(1, np.maximum(0, total/SAMPLE_RATE-time)/2)
    mono = (pad+bell)*fade
    return np.asarray([mono, mono*.96], dtype=np.float32)


def add_titles(pixels, index, count, title, endline):
    text = title if title and index < FPS*5 else endline if endline and index >= count-FPS*5 else ''
    if not text:
        return pixels
    image = Image.fromarray(pixels)
    width, height = image.size
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', max(16, int(width*.048)))
    draw = ImageDraw.Draw(image)
    box = draw.textbbox((0, 0), text, font=font)
    position = ((width-(box[2]-box[0]))//2, int(height*.11 if index < FPS*5 else height*.8))
    draw.text(position, text, font=font, fill=(250, 240, 213), stroke_width=2, stroke_fill=(24, 20, 16))
    return np.asarray(image)


def assemble(paths, destination, *, seconds_each=15, title='纸翼归航', endline='归处，是有人等你。', score=True):
    paths, destination = [Path(path) for path in paths], Path(destination)
    if not paths or not 0 < seconds_each <= 30 or seconds_each*FPS != int(seconds_each*FPS):
        raise ValueError('镜头时长必须是有效的整帧时长')
    if destination.exists():
        raise FileExistsError(destination)
    per_clip = int(seconds_each*FPS)
    frame_count, sample_count = per_clip*len(paths), per_clip*SAMPLES_PER_FRAME
    with av.open(str(paths[0])) as first:
        width, height = first.streams.video[0].width, first.streams.video[0].height
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem+'.partial.mp4')
    frame_index = 0
    try:
        with av.open(str(temporary), 'w', options={'movflags': '+faststart'}) as output:
            video = output.add_stream('libx264', rate=FPS)
            video.width, video.height, video.pix_fmt = width, height, 'yuv420p'
            video.options = {'crf': '18', 'preset': 'medium'}
            audio = output.add_stream('aac', rate=SAMPLE_RATE)
            audio.layout, audio.bit_rate = 'stereo', 192000
            for clip_index, path in enumerate(paths):
                with av.open(str(path)) as source:
                    stream = source.streams.video[0]
                    if (stream.width, stream.height, stream.average_rate) != (width, height, Fraction(FPS)):
                        raise ValueError(f'镜头规格不一致：{path}')
                    if stream.frames and stream.frames < per_clip:
                        raise ValueError(f'镜头帧数不足，禁止冻结或循环补帧：{path}')
                    sound = read_audio(path, sample_count)
                    decoded = source.decode(video=0)
                    for local_index in range(per_clip):
                        original = next(decoded, None)
                        if original is None:
                            raise ValueError(f'镜头帧数不足，禁止冻结或循环补帧：{path}')
                        pixels = add_titles(original.to_ndarray(format='rgb24'), frame_index, frame_count, title, endline)
                        frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
                        frame.pts, frame.time_base = frame_index, Fraction(1, FPS)
                        for packet in video.encode(frame):
                            output.mux(packet)
                        start = local_index*SAMPLES_PER_FRAME
                        block = sound[:, start:start+SAMPLES_PER_FRAME].copy()
                        if score:
                            block += score_block(frame_index*SAMPLES_PER_FRAME, SAMPLES_PER_FRAME, frame_count*SAMPLES_PER_FRAME)
                        block = np.ascontiguousarray(np.clip(block, -.98, .98), dtype=np.float32)
                        sample = av.AudioFrame.from_ndarray(block, format='fltp', layout='stereo')
                        sample.sample_rate, sample.pts, sample.time_base = SAMPLE_RATE, frame_index*SAMPLES_PER_FRAME, Fraction(1, SAMPLE_RATE)
                        for packet in audio.encode(sample):
                            output.mux(packet)
                        frame_index += 1
                print(json.dumps({'completedShots': clip_index+1, 'totalShots': len(paths), 'frames': frame_index}), flush=True)
            for stream in (video, audio):
                for packet in stream.encode():
                    output.mux(packet)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {'path': str(destination.resolve()), 'width': width, 'height': height, 'frames': frame_count, 'fps': FPS, 'seconds': frame_count/FPS}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path, help='JSON with ordered clips:[absolute path,...] and output')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    print(json.dumps(assemble(manifest['clips'], manifest['output'], seconds_each=manifest.get('secondsEach', 15),
                              title=manifest.get('title', '纸翼归航'), endline=manifest.get('endline', '归处，是有人等你。'), score=manifest.get('score', True)), ensure_ascii=False))
