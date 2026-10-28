import importlib.util
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import composition


def source(path, *, fps=24, size=(64, 36), audio=True, gradient=False):
    with av.open(str(path), 'w') as output:
        video = output.add_stream('libx264', rate=fps)
        video.width, video.height, video.pix_fmt = *size, 'yuv420p'
        video.options = {'crf': '12', 'preset': 'ultrafast'}
        sound = output.add_stream('aac', rate=48000) if audio else None
        if sound: sound.layout = 'stereo'
        for index in range(fps):
            color = (20+index*5, 12, 12) if gradient else (12, 20, 230)
            frame = av.VideoFrame.from_ndarray(np.full((size[1], size[0], 3), color, dtype=np.uint8), format='rgb24')
            frame.pts, frame.time_base = index, Fraction(1, fps)
            for packet in video.encode(frame): output.mux(packet)
            if sound:
                count = 48000//fps
                samples = (.1*np.sin(np.arange(index*count, (index+1)*count)*2*np.pi*440/48000)).astype('float32')
                block = av.AudioFrame.from_ndarray(np.stack([samples, samples]), format='fltp', layout='stereo')
                block.sample_rate, block.pts = 48000, index*count
                for packet in sound.encode(block): output.mux(packet)
        for stream in [video]+([sound] if sound else []):
            for packet in stream.encode(): output.mux(packet)


class CompositionTests(unittest.TestCase):
    def test_real_trim_order_and_unequal_durations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source(root/'red.mp4', gradient=True);source(root/'blue.mp4')
            result = composition.render_composition({'clips': [
                {'path': str(root/'blue.mp4'), 'startSeconds': 0, 'durationSeconds': .5},
                {'path': str(root/'red.mp4'), 'startSeconds': .25, 'durationSeconds': .25}],
                'output': str(root/'film.mp4')})
            with av.open(str(root/'film.mp4')) as movie:
                frames = [f.to_ndarray(format='rgb24') for f in movie.decode(video=0)]
            self.assertEqual((result['frames'], result['durationSeconds'], len(frames)), (18, .75, 18))
            self.assertGreater(frames[6][..., 2].mean(), 200)
            self.assertAlmostEqual(frames[12][..., 0].mean(), 50, delta=8)
            self.assertGreater(frames[-1][..., 0].mean(), frames[12][..., 0].mean()+12)
            with av.open(str(root/'film.mp4')) as movie:
                self.assertAlmostEqual(float(movie.streams.audio[0].duration*movie.streams.audio[0].time_base), .75, places=2)
                self.assertGreater(max(abs(f.to_ndarray()).max() for f in movie.decode(audio=0)), .02)

    def test_mixed_fps_size_and_silent_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source(root/'a.mp4', fps=30, size=(80, 40), audio=False);source(root/'b.mp4', fps=24, size=(36, 64), audio=False)
            result=composition.render_composition({'clips': [{'path':str(root/'a.mp4'),'startSeconds':.2,'durationSeconds':.5},
                {'path':str(root/'b.mp4'),'startSeconds':0,'durationSeconds':.25}], 'output':str(root/'out.mp4')})
            self.assertEqual((result['width'],result['height'],result['frames']),(80,40,18))
            with av.open(str(root/'out.mp4')) as movie:
                frames=list(movie.decode(video=0));self.assertEqual(len(frames),18)
                self.assertEqual(movie.streams.video[0].average_rate,24)
                pixels=frames[-1].to_ndarray(format='rgb24')
                self.assertLess(pixels[:,0].max(),5)
                self.assertGreater(pixels[:,40,2].mean(),200)

    def test_overrun_fails_without_output_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source(root/'a.mp4');before=(root/'a.mp4').read_bytes()
            with self.assertRaisesRegex(ValueError,'时长|范围'):
                composition.render_composition({'clips':[{'path':str(root/'a.mp4'),'startSeconds':.8,'durationSeconds':.5}], 'output':str(root/'out.mp4')})
            self.assertFalse((root/'out.mp4').exists());self.assertEqual((root/'a.mp4').read_bytes(),before)

    def test_invalid_request_keys_and_numbers_are_rejected(self):
        for clip in [{'path':'C:/secret.mp4','durationSeconds':1}, {'url':'http://example.com/a.mp4','durationSeconds':1},
                     {'storageKey':'video:a','startSeconds':-1,'durationSeconds':1},
                     {'storageKey':'video:a','durationSeconds':float('nan')},
                     {'storageKey':'video:a','durationSeconds':True}]:
            with self.subTest(clip=clip),self.assertRaises(ValueError):composition.normalize_request({'clips':[clip]})

    def test_request_retains_order_and_frame_quantizes_duration(self):
        value=composition.normalize_request({'clips':[{'storageKey':'video:b','startSeconds':.2,'durationSeconds':.1},
            {'storageKey':'inline:a','durationSeconds':.5}]})
        self.assertEqual([c['storageKey'] for c in value['clips']],['video:b','inline:a'])
        self.assertEqual(value['clips'][0]['durationSeconds'],2/24)
        with self.assertRaises(ValueError):composition.normalize_request({'clips':[{'storageKey':'video:a','durationSeconds':1801}]})

    def test_unsupported_container_is_rejected_even_with_mp4_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'disguised.mp4'
            Image.new('RGB',(64,36),'red').save(path,format='GIF',save_all=True,append_images=[Image.new('RGB',(64,36),'blue')],duration=500,loop=0)
            with self.assertRaisesRegex(ValueError,'格式'):composition.probe(path)
