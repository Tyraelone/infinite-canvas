import importlib.util
import tempfile
import unittest
from pathlib import Path

import av
import numpy as np

spec = importlib.util.spec_from_file_location('film_edit', Path(__file__).parents[1] / 'film_edit.py')
film_edit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(film_edit)


def clip(path, color):
    with av.open(str(path), 'w') as output:
        video = output.add_stream('libx264', rate=24)
        video.width, video.height, video.pix_fmt = 64, 36, 'yuv420p'
        video.options = {'crf': '18', 'preset': 'ultrafast'}
        audio = output.add_stream('aac', rate=48000)
        audio.layout = 'stereo'
        for index in range(12):
            frame = av.VideoFrame.from_ndarray(np.full((36, 64, 3), color, dtype=np.uint8), format='rgb24')
            frame.pts = index
            for packet in video.encode(frame):
                output.mux(packet)
            samples = np.repeat((np.sin(np.arange(index*2000, (index+1)*2000) * 2*np.pi*440/48000) * .1)[None, :], 2, axis=0).astype('float32')
            sound = av.AudioFrame.from_ndarray(samples, format='fltp', layout='stereo')
            sound.sample_rate, sound.pts = 48000, index*2000
            for packet in audio.encode(sound):
                output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode():
                output.mux(packet)


class FilmEditTests(unittest.TestCase):
    def test_two_real_clips_preserve_frame_order_duration_and_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip(root/'red.mp4', (230, 10, 10))
            clip(root/'blue.mp4', (10, 10, 230))
            film_edit.assemble([root/'red.mp4', root/'blue.mp4'], root/'film.mp4', seconds_each=.5, title='', endline='', score=False)
            with av.open(str(root/'film.mp4')) as film:
                frames = [frame.to_ndarray(format='rgb24') for frame in film.decode(video=0)]
                self.assertEqual(len(frames), 24)
                self.assertGreater(frames[6][..., 0].mean(), frames[6][..., 2].mean() + 100)
                self.assertGreater(frames[18][..., 2].mean(), frames[18][..., 0].mean() + 100)
            with av.open(str(root/'film.mp4')) as film:
                self.assertAlmostEqual(float(film.streams.video[0].duration * film.streams.video[0].time_base), 1.0, places=3)
                self.assertGreater(max(float(np.max(np.abs(f.to_ndarray()))) for f in film.decode(audio=0)), .01)

    def test_short_clip_fails_without_freezing_or_repeating_to_fill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip(root/'short.mp4', (230, 10, 10))
            with self.assertRaisesRegex(ValueError, '帧数不足'):
                film_edit.assemble([root/'short.mp4'], root/'film.mp4', seconds_each=1, title='', endline='', score=False)
            self.assertFalse((root/'film.mp4').exists())


if __name__ == '__main__':
    unittest.main()
