from core.config import Settings
from core.schemas import StreamMode
from transcoder.ffmpeg_cmd import build_ffmpeg_argv


def _settings() -> Settings:
    return Settings(
        database_url="postgresql://x",
        storage_dir="/tmp/t",
    )


def test_vod_has_required_flags():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4", "/tmp/out", StreamMode.VOD, _settings()
    )
    assert "ffmpeg" == argv[0]
    assert "-re" in argv
    assert "-nostats" in argv
    assert argv[argv.index("-progress") + 1] == "pipe:1"
    assert "-hls_playlist_type" in argv
    assert argv[argv.index("-hls_playlist_type") + 1] == "event"
    assert "temp_file" in argv[argv.index("-hls_flags") + 1]
    assert argv[-1] == "/tmp/out/playlist.m3u8"


def test_live_has_sliding_window_flags():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4", "/tmp/out", StreamMode.LIVE, _settings()
    )
    flags = argv[argv.index("-hls_flags") + 1]
    assert "delete_segments" in flags
    assert "omit_endlist" in flags
    assert "independent_segments" in flags
    assert "temp_file" in flags
    assert "-hls_list_size" in argv
    assert argv[argv.index("-hls_list_size") + 1] == "6"


def test_audio_normalized_to_stereo_48k():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4", "/tmp/out", StreamMode.VOD, _settings()
    )
    assert argv[argv.index("-ac") + 1] == "2"
    assert argv[argv.index("-ar") + 1] == "48000"
    assert argv[argv.index("-c:a") + 1] == "aac"


def test_force_keyframes_expr_uses_segment_seconds():
    s = _settings()
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4", "/tmp/out", StreamMode.VOD, s
    )
    expr = argv[argv.index("-force_key_frames") + 1]
    assert f"n_forced*{s.hls_segment_seconds}" in expr


def test_input_url_is_used_as_is():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4?sig=abc", "/tmp/out", StreamMode.VOD, _settings()
    )
    assert argv[argv.index("-i") + 1] == "https://example.com/a.mp4?sig=abc"


def test_loop_adds_stream_loop_before_input():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.LIVE,
        _settings(),
        loop=True,
    )
    # -stream_loop -1 must appear BEFORE -i
    sl_idx = argv.index("-stream_loop")
    assert argv[sl_idx + 1] == "-1"
    i_idx = argv.index("-i")
    assert sl_idx < i_idx


def test_realtime_false_omits_dash_re():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.VOD,
        _settings(),
        realtime=False,
    )
    assert "-re" not in argv


def test_realtime_default_true_includes_dash_re():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4", "/tmp/out", StreamMode.VOD, _settings()
    )
    assert "-re" in argv


def test_video_height_adds_scale_filter():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.VOD,
        _settings(),
        video_height=720,
    )
    vf_idx = argv.index("-vf")
    assert argv[vf_idx + 1] == "scale=-2:720"


def test_video_height_none_omits_scale_filter():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4", "/tmp/out", StreamMode.VOD, _settings()
    )
    assert "-vf" not in argv


def test_video_bitrate_sets_b_maxrate_bufsize():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.VOD,
        _settings(),
        video_bitrate="2M",
    )
    assert argv[argv.index("-b:v") + 1] == "2M"
    assert argv[argv.index("-maxrate") + 1] == "2M"
    assert argv[argv.index("-bufsize") + 1] == "4M"


def test_video_bitrate_k_suffix_doubles_bufsize():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.VOD,
        _settings(),
        video_bitrate="2500k",
    )
    assert argv[argv.index("-bufsize") + 1] == "5000k"


def test_video_bitrate_no_suffix_doubles_bufsize():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.VOD,
        _settings(),
        video_bitrate="800",
    )
    assert argv[argv.index("-b:v") + 1] == "800"
    assert argv[argv.index("-bufsize") + 1] == "1600"


def test_video_bitrate_none_omits_rate_control():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4", "/tmp/out", StreamMode.VOD, _settings()
    )
    assert "-b:v" not in argv
    assert "-maxrate" not in argv
    assert "-bufsize" not in argv


def test_loop_false_does_not_add_stream_loop():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.LIVE,
        _settings(),
        loop=False,
    )
    assert "-stream_loop" not in argv
