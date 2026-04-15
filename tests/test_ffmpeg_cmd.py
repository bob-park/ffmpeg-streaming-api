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


def test_loop_false_does_not_add_stream_loop():
    argv = build_ffmpeg_argv(
        "https://example.com/a.mp4",
        "/tmp/out",
        StreamMode.LIVE,
        _settings(),
        loop=False,
    )
    assert "-stream_loop" not in argv
