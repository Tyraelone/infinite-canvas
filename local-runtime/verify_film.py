"""Decode a complete local film and its twelve real source shots for acceptance."""
import argparse
import hashlib
import json
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def inspect(path, take=7):
    path = Path(path)
    times, black_frames, still, previous, motion_peak = [], 0, None, None, 0.0
    with av.open(str(path)) as source:
        video = source.streams.video[0]
        container_seconds = source.duration/av.time_base if source.duration is not None else None
        width, height, fps = video.width, video.height, float(video.average_rate)
        for index, frame in enumerate(source.decode(video=0)):
            times.append(float(frame.pts*frame.time_base))
            pixels = frame.to_ndarray(format='rgb24')
            black_frames += int(pixels.max() < 4)
            if index % round(fps) == 0:
                sample = pixels[::12, ::12].astype(np.float32)
                if previous is not None:
                    motion_peak = max(motion_peak, float(np.mean(np.abs(sample-previous))))
                previous = sample
            if index == round(take*fps):
                still = Image.fromarray(pixels)
    if len(times) > 1:
        assert np.max(np.abs(np.diff(times)-1/fps)) < .0001, f'Non-CFR source: {path}'
    with av.open(str(path)) as source:
        assert source.streams.audio, f'Missing audio: {path}'
        audio = source.streams.audio[0]
        assert abs(float((audio.start_time or 0)*audio.time_base)-times[0]) < .025, f'Audio start offset: {path}'
        peak, energy, samples = 0, 0, 0
        for frame in source.decode(audio=0):
            values = frame.to_ndarray().astype(np.float64)
            peak = max(peak, float(np.max(np.abs(values))))
            energy += float(np.sum(values**2))
            samples += values.size
        audio_seconds = float(audio.duration*audio.time_base) if audio.duration is not None else None
    assert peak > .00001 and samples, f'Empty sound: {path}'
    assert black_frames == 0, f'Black frames: {path}'
    assert motion_peak > .15, f'No visible motion to review: {path}'
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size,
            'width': width, 'height': height, 'frames': len(times), 'fps': fps, 'seconds': len(times)/fps,
            'firstPts': times[0], 'lastPts': times[-1], 'blackFrames': black_frames, 'motionPeak': motion_peak, 'containerSeconds': container_seconds,
            'audioSeconds': audio_seconds, 'audioPeak': peak, 'audioRms': (energy/samples)**.5}, still


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    assert len(manifest['clips']) == 12
    shots, stills = [], []
    for path in manifest['clips']:
        shot, still = inspect(path)
        assert shot['fps'] == 24 and shot['frames'] >= 360 and shot['seconds'] >= 15
        shots.append(shot)
        stills.append(still)
    assert len({shot['sha256'] for shot in shots}) == 12, 'Repeated source clip'
    film, _ = inspect(manifest['output'])
    assert film['frames'] == 4320 and film['fps'] == 24 and film['seconds'] == 180
    assert abs(film['audioSeconds']-180) < .05
    assert abs(film['containerSeconds']-180) < .05
    assert film['lastPts'] >= 179.95
    sheet = Image.new('RGB', (1280, 3*205), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
    for index, still in enumerate(stills):
        assert still is not None
        still.thumbnail((312, 175))
        x, y = (index % 4)*320+4, (index//4)*205
        sheet.paste(still, (x, y))
        draw.text((x+5, y+178), f'{index+1:02d}  {index*15:03d}-{(index+1)*15:03d}s', fill=(245, 235, 210), font=font)
    output = Path(manifest['output'])
    contact = output.with_suffix('.contact.jpg')
    sheet.save(contact, quality=95)
    report = {'canvasId': manifest.get('canvasId'), 'title': manifest.get('title'), 'sources': shots, 'film': film,
              'contactSheet': str(contact), 'complete': True}
    report_path = output.with_suffix('.verification.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(report_path), 'contactSheet': str(contact), **film}, ensure_ascii=False))


if __name__ == '__main__':
    main()
