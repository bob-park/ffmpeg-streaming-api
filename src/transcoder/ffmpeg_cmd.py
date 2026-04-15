from core.config import Settings
from core.schemas import StreamMode


def build_ffmpeg_argv(
    source_url: str,
    output_dir: str,
    mode: StreamMode,
    settings: Settings,
    loop: bool = False,
) -> list[str]:
    """Build ffmpeg argv for HLS transcoding with 1x (-re) pacing.

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
        "-re",
    ]
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
