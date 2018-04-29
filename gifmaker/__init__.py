'''
Created on Mar 30, 2014

@author: ignacio
'''
from collections import namedtuple
import json
import logging
import os
import re
import subprocess
import tempfile
from argparse import ArgumentParser, ArgumentError


RE_VIDEO_RES = re.compile(r'Video:.*? (\d+x\d+)[, ]', re.MULTILINE|re.DOTALL)
RE_VIDEO_FPS = re.compile(r'Video:.*? ([\d.]+) fps', re.MULTILINE|re.DOTALL)

VideoData = namedtuple('VideoData', ['path', 'width', 'height', 'fps'])


def _get_arg_parser():
    parser = ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("-s", "--start", type=start_time, default=None,
                        help='Start of the gif, in seconds (or M:S, H:M:S '
                        'format). Defaults to 0')
    parser.add_argument("-d", "--duration", type=int, default=None,
                        help='Duration of the gif, in seconds.')
    parser.add_argument("-l", "--loop", action='store_true', default=False,
                        help='Looping gif?')
    parser.add_argument("--scale", type=float, default=1,
                        help='Ratio to scale the output. Defaults to 1')
    parser.add_argument("--frameskip", default=None,
                        help='Ratio of skipped frames in format A/B. Defaults '
                        'to 0 (none skipped)')
    parser.add_argument("--speed", type=float, default=1,
                        help='Speed factor. Defaults to 1')
    parser.add_argument("--no-optimize", action='store_false', dest='optimize',
                        default=True, help='Do NOT optimize the resulting gif')
    parser.add_argument("-f", "--fuzz", type=int, default=None,
                        help='Fuzz percentage for gif creation. '
                        'Defaults to none')
    parser.add_argument("--crop", type=CropArea.from_arg, default=None,
                        help=("Rectangular area to crop from the input, "
                              "in format width:height:x:y. Accepts "
                              "relative and absolute values."))
    parser.add_argument("-r", "--reverse", action='store_true', default=False,
                        help='Reverse frames?')
    return parser


class CropArea():
    def __init__(self, width, height, xpos, ypos):
        values = [width, height, xpos, ypos]
        if any(x < 0 for x in values):
            raise ValueError("Some dimension is negative")
        self._percentages = all(x <= 1 for x in values)
        self._width = width
        self._height = height
        self._xpos = xpos
        self._ypos = ypos

    def crop_argument(self, width, height, scale=None):
        return ":".join(map(str, self._get_values(width, height, scale)))

    def _get_values(self, width, height, scale):
        if self._percentages:
            values = [self._width * width, self._height * height,
                      self._xpos * width, self._ypos * height]
        else:
            values = [self._width, self._height, self._xpos, self._ypos]
        if scale is not None:
            values = [v * scale for v in values]
        return values

    @classmethod
    def from_arg(cls, arg):
        try:
            width, height, xpos, ypos = map(float, arg.split(":"))
        except ValueError:
            raise ValueError("Invalid crop argument: '{}'".format(arg))
        return cls(width, height, xpos, ypos)


def start_time(start):
    res = 0
    for split in start.split(":"):
        res *= 60
        res += float(split)
    return res


def _parse_args():
    options = _get_arg_parser().parse_args()
    if options.frameskip is not None:
        try:
            options.frameskip = [int(x) for x in options.frameskip.split("/")]
        except Exception:
            raise ArgumentError("Wrong frameskip format: '%s'"
                                % options.frameskip)
    return options

def _exec(*args):
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = proc.stdout.read()
    try:
        # output might be `bytes`
        return output.decode("utf-8")
    except AttributeError:
        # if `output.decode` doesn't exist, output should already be a `str`
        return output

def _extract_video_data(video):
    output = _exec("avprobe", video)
    width, height = RE_VIDEO_RES.search(output).group(1).split("x")
    fps = RE_VIDEO_FPS.search(output).group(1)
    data = VideoData(path=video, width=int(width), height=int(height),
                     fps=round(float(fps)))
    return data


def _extract_frames(video_data, output_dir, start=None, duration=None,
                    scale=None, crop=None):
    command = ['avconv']
    if start is not None:
        command += ['-ss', str(start)]
    command += ['-i', video_data.path]
    if duration is not None:
        command += ['-t', str(duration)]
    if crop is not None:
        command += ['-vf', 'crop=%s' % crop.crop_argument(video_data.width,
                                                          video_data.height,
                                                          scale)]
    if scale is not None:
        scaled_height = int(round(video_data.height * scale))
        scaled_width = int(round(video_data.width * scale))
        command += ['-s', '%sx%s' % (scaled_width, scaled_height)]
    command.append(os.path.join(output_dir, 'frames%05d.png'))
    logging.info("Running command: %s", command)
    subprocess.call(command)


def _make_gif(frames_dir, output, fps, options, start_frame=None,
              end_frame=None):
    frames = sorted(os.listdir(frames_dir))
    if start_frame is None:
        start_frame = 0
    if end_frame is None:
        end_frame = len(frames)
    skipped, every = options.frameskip or [0, 1]
    rate = 1. * every / (every - skipped)
    frame = start_frame
    used_frames = []
    real_fps = round(fps) * options.speed / rate

    command = ['convert', '-delay', '1x%.2f' % real_fps]
    if options.loop:
        command += ['-loop', '0']
    if options.fuzz is not None:
        command += ['-fuzz', '%s%%' % options.fuzz]
    if options.optimize:
        command += ['-layers', 'optimize']

    while int(frame) < end_frame:
        used_frames.append(frames[int(frame)])
        frame += rate

    if options.reverse:
        used_frames = reversed(used_frames)
    command += [os.path.join(frames_dir, f) for f in used_frames]
    command.append(output)
    logging.info("Running command: %s", command)
    subprocess.call(command)


def _human_size(size, format='.2f', max_value=1000):
    for unit in ['b', 'Kb', 'Mb', 'Gb', 'Tb', 'Pb']:
        if abs(size) < max_value:
            break
        size /= 1024.
    format_str = "{{:{}}} {{}}".format(format)
    return format_str.format(size, unit)


def main():
    logging.basicConfig(level=logging.INFO)
    options = _parse_args()
    logging.info("Extracting video data from '%s'", options.input)
    data = _extract_video_data(options.input)
    logging.info("Data: %s", data)
    tmp_dir = tempfile.mkdtemp()
    logging.info("Temporal dir: '%s'", tmp_dir)
    try:
        logging.info("Extracting frames...")
        _extract_frames(data, tmp_dir, options.start, options.duration,
                        options.scale, options.crop)
        logging.info("Got %s frames...", len(os.listdir(tmp_dir)))
        logging.info("Making output gif: '%s'", options.output)
        _make_gif(tmp_dir, options.output, data.fps, options)
        logging.info("Done. Final size: %s",
                     _human_size(os.path.getsize(options.output)))
    finally:
        os.system("rm -rf %s" % tmp_dir)


if __name__ == "__main__":
    main()
