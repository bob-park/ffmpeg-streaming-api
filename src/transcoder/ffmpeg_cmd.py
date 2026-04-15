from core.config import Settings
from core.schemas import StreamMode


def _double_bitrate(br: str) -> str:
    """Return `br` with its numeric portion doubled, preserving the k/M suffix.

    Used to derive a sane -bufsize (2x bitrate) from the validated video_bitrate
    string. Assumes the caller has already validated the format.
    """
    suffix = ""
    if br and br[-1] in "kKmM":
        suffix = br[-1]
        num = br[:-1]
    else:
        num = br
    return f"{int(num) * 2}{suffix}"


def build_ffmpeg_argv(
    source_url: str,
    output_dir: str,
    mode: StreamMode,
    settings: Settings,
    loop: bool = False,
    realtime: bool = True,
    video_bitrate: str | None = None,
    video_height: int | None = None,
) -> list[str]:
    """Build ffmpeg argv for HLS transcoding.

    When realtime=True (default), `-re` paces input at 1x wall-clock speed —
    required for live streams so ffmpeg doesn't consume the source faster than
    real time. When realtime=False, `-re` is omitted so ffmpeg transcodes as
    fast as the machine allows (typical for VOD/batch work).
    video_bitrate (e.g. "2500k", "2M") sets target video bitrate with matching
    -maxrate and a 2x -bufsize for HLS-friendly VBR. If None, libx264 defaults
    to CRF rate control.
    video_height scales the output to the given height while preserving aspect
    ratio (width auto-computed to be divisible by 2 via scale=-2:H). If None,
    the source resolution is passed through unchanged.
    Keyframes are forced via -force_key_frames expr (fps-agnostic).
    Live mode uses sliding window; VOD mode keeps all segments.
    If loop=True, `-stream_loop -1` is inserted before `-i` so ffmpeg re-reads
    the input from the start forever. Requires a seekable input (HTTP range or
    local file); does NOT work on chunked-transfer streams.
    """
    segment_seconds = settings.hls_segment_seconds
    argv: list[str] = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        "-nostats",
        "-progress",
        "pipe:1",
    ]
    if realtime:
        argv.append("-re")
    if loop:
        argv += ["-stream_loop", "-1"]
    argv += [
        "-i",
        source_url,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        # video
        "-c:v",
        "libx264",
        "-preset",
        settings.x264_preset,
        "-profile:v",
        settings.x264_profile,
        "-pix_fmt",
        "yuv420p",
        "-sc_threshold",
        "0",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{segment_seconds})",
    ]
    if video_height is not None:
        # scale=-2:H preserves aspect ratio, width rounded to nearest even.
        argv += ["-vf", f"scale=-2:{video_height}"]
    if video_bitrate is not None:
        # VBR with a hard cap: target == max, bufsize = 2x for HLS chunk smoothing.
        argv += [
            "-b:v", video_bitrate,
            "-maxrate", video_bitrate,
            "-bufsize", _double_bitrate(video_bitrate),
        ]
    argv += [
        # audio (normalized to stereo 48kHz AAC)
        "-c:a",
        "aac",
        "-b:a",
        settings.audio_bitrate,
        "-ac",
        str(settings.audio_channels),
        "-ar",
        str(settings.audio_sample_rate),
        # HLS muxer
        "-f",
        "hls",
        "-hls_time",
        str(segment_seconds),
        "-hls_segment_filename",
        f"{output_dir}/seg_%05d.ts",
    ]
    if mode == StreamMode.LIVE:
        argv += [
            "-hls_list_size",
            str(settings.hls_live_list_size),
            "-hls_flags",
            "delete_segments+omit_endlist+independent_segments+temp_file",
        ]
    else:  # VOD
        argv += [
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_flags",
            "temp_file",
        ]
    argv += [f"{output_dir}/playlist.m3u8"]
    return argv
