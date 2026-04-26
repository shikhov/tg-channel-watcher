import base64
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from telethon import types


ROOT = Path(__file__).resolve().parents[1]
APP_SRC = ROOT / "app" / "src"
TEST_ENV_PATH = ROOT / "tests" / "test.env"

sys.path.insert(0, str(APP_SRC))

import app  # noqa: E402
from app import Watcher  # noqa: E402


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5Wg4kAAAAASUVORK5CYII="
)
WEBP_BYTES = base64.b64decode(
    "UklGRiIAAABXRUJQVlA4IBYAAAAQAgCdASoBAAEAAUAmJaACdLoB+AADsAD+8ut//NgVzXPv9//S4P0uD9LgAAA="
)


def load_test_env():
    if not TEST_ENV_PATH.exists():
        return
    for line in TEST_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def normalize_channel(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def get_test_channel(env_name):
    value = os.getenv(env_name)
    if value is None:
        pytest.skip(
            f"Missing test channel. Set {env_name}"
        )
    return normalize_channel(value)


def clear_channel(client, channel):
    messages = client.get_messages(channel, limit=100)
    if not messages:
        return
    client.delete_messages(channel, [message.id for message in messages])
    time.sleep(1)


def write_file(folder, name, content):
    path = folder / name
    path.write_bytes(content)
    return path


def prepare_profiles(db, profiles):
    db.profiles.insert_many([{**profile, "enable": True} for profile in profiles])
    for profile in profiles:
        db.counters.insert_one(
            {
                "profile_id": profile["_id"],
                "profile_name": profile["name"],
                "counters": {str(channel): 1 for channel in profile["channels"]},
            }
        )


def run_profiles_once(watcher):
    watcher.logger.debug_mode = True
    watcher.telethon_logger.setLevel(logging.WARNING)
    watcher.run_profiles()
    time.sleep(3)


def recent_output_messages(client, output_channel, limit=100):
    return client.get_messages(output_channel, min_id=1, limit=limit)


def messages_with_marker(client, output_channel, marker, limit=100):
    return [
        message
        for message in recent_output_messages(client, output_channel, limit)
        if marker in (message.message or "")
    ]


def wait_for_marker_count(client, output_channel, marker, expected_count, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        messages = messages_with_marker(client, output_channel, marker)
        if len(messages) >= expected_count:
            return messages
        time.sleep(1)
    pytest.fail(
        f"Timed out waiting for marker {marker!r}, expected at least {expected_count} messages."
    )


def send_sticker_or_skip(client, input_channel, sticker_path):
    try:
        return client.send_file(
            input_channel,
            sticker_path,
            attributes=[
                types.DocumentAttributeImageSize(1, 1),
                types.DocumentAttributeSticker(
                    alt="test",
                    stickerset=types.InputStickerSetEmpty(),
                ),
            ],
        )
    except Exception as exc:
        pytest.skip(f"Unable to send sticker to test channel: {exc}")


@pytest.fixture(scope="session")
def runtime():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_test_env()

    CONNSTRING = os.getenv("CONNSTRING")
    DBNAME = os.getenv("DBNAME")

    session_dir = tempfile.TemporaryDirectory()
    cwd = os.getcwd()
    try:
        os.chdir(session_dir.name)
        watcher = Watcher(CONNSTRING, DBNAME)
    finally:
        os.chdir(cwd)

    db = watcher.db
    client = watcher.client
    input_channel = get_test_channel(env_name="TG_TEST_INPUT_CHANNEL")
    output_channel = get_test_channel(env_name="TG_TEST_OUTPUT_CHANNEL")
    watcher.DELAY = 0

    runtime_data = {
        "watcher": watcher,
        "db": db,
        "client": client,
        "input_channel": input_channel,
        "output_channel": output_channel,
        "session_dir": session_dir,
    }

    yield runtime_data

    try:
        # clear_channel(client, input_channel)
        # clear_channel(client, output_channel)
        db.profiles.delete_many({})
        db.counters.delete_many({})
    finally:
        client.disconnect()
        session_dir.cleanup()


@pytest.fixture()
def clean_state(runtime):
    clear_channel(runtime["client"], runtime["input_channel"])
    clear_channel(runtime["client"], runtime["output_channel"])
    runtime["db"].profiles.delete_many({})
    runtime["db"].counters.delete_many({})
    runtime["watcher"].sent = {}
    return runtime


def test_rules(clean_state):
    runtime = clean_state
    marker = f"case-{uuid.uuid4().hex[:8]}"
    profiles = [
        {
            "_id": "dupes",
            "name": "dupes",
            "channels": [runtime["input_channel"]],
            "output": [
                {
                    "rules": [
                        {"regex": rf"{marker} duplicate ekb"}
                    ],
                    "any_matching": True,
                    "filter_dupes": True,
                    "output_channel": runtime["output_channel"],
                }
            ],
        },
        {
            "_id": "regex_eval",
            "name": "regex_eval",
            "channels": [runtime["input_channel"]],
            "output": [
                {
                    "rules": [
                        {
                            "regex": rf"{marker} iphone (16|17) pro"
                        },
                        {
                            "regex": r"(\d+)\s?\$",
                            "eval": "int(m.group(1)) <= 800"
                        },
                    ],
                    "any_matching": False,
                    "output_channel": runtime["output_channel"],
                }
            ],
        },
        {
            "_id": "eval_file",
            "name": "eval_file",
            "channels": [runtime["input_channel"]],
            "output": [
                {
                    "rules": [
                        {
                            "eval": f"msg.file and msg.file.name and msg.file.name == '{marker}.txt'"
                        }
                    ],
                    "any_matching": True,
                    "output_channel": runtime["output_channel"],
                }
            ],
        },
        {
            "_id": "hide_forward",
            "name": "hide_forward",
            "channels": [runtime["input_channel"]],
            "output": [
                {
                    "rules": [
                        {
                            "regex": rf"{marker} hidden"
                        }
                    ],
                    "any_matching": True,
                    "hide_forward": True,
                    "output_channel": runtime["output_channel"],
                }
            ],
        },
        {
            "_id": "exec_transform",
            "name": "exec_transform",
            "channels": [runtime["input_channel"]],
            "output": [
                {
                    "rules": [
                        {
                            "regex": rf"{marker} wiki"
                        }
                    ],
                    "any_matching": True,
                    "exec": True,
                    "exec_code": (
                        "item.msg.text = item.msg.text.replace('wiki', 'encyclo')\n"
                        "if item.msg.media:\n"
                        "    item.msg.media = None\n"
                        "    item.msg.text += f'\\n<{item.count} media was removed>'\n"
                        "    item.count = 1\n"
                    ),
                    "output_channel": runtime["output_channel"],
                }
            ],
        },
    ]
    prepare_profiles(runtime["db"], profiles)

    with tempfile.TemporaryDirectory() as tmp_dir:
        folder = Path(tmp_dir)
        attachment_path = write_file(folder, f"{marker}.txt", b"attachment-body")
        exec_attachment_path = write_file(folder, f"{marker}-exec.txt", b"exec-body")

        runtime["client"].send_message(runtime["input_channel"], f"{marker} duplicate ekb")
        runtime["client"].send_message(runtime["input_channel"], f"{marker} duplicate ekb")
        runtime["client"].send_message(runtime["input_channel"], f"{marker} iphone 16 pro 799$")
        runtime["client"].send_message(runtime["input_channel"], f"{marker} iphone 17 pro 850$")
        runtime["client"].send_file(
            runtime["input_channel"],
            attachment_path,
            caption=f"{marker} file message",
            force_document=True,
        )
        runtime["client"].send_message(runtime["input_channel"], f"{marker} hidden")
        runtime["client"].send_file(
            runtime["input_channel"],
            exec_attachment_path,
            caption=f"{marker} wiki",
            force_document=True,
        )

        run_profiles_once(runtime["watcher"])

    messages = wait_for_marker_count(
        runtime["client"], runtime["output_channel"], marker, expected_count=5
    )

    duplicate_messages = [
        message for message in messages if message.message == f"{marker} duplicate ekb"
    ]
    assert len(duplicate_messages) == 1
    assert getattr(duplicate_messages[0], "fwd_from", None) is not None

    price_messages_yes = [
        message for message in messages if message.message == f"{marker} iphone 16 pro 799$"
    ]
    assert len(price_messages_yes) == 1
    assert getattr(price_messages_yes[0], "fwd_from", None) is not None
    
    price_messages_no = [
        message for message in messages if message.message == f"{marker} iphone 17 pro 850$"
    ]
    assert len(price_messages_no) == 0

    file_messages = [message for message in messages if message.message == f"{marker} file message"]
    assert len(file_messages) == 1
    assert file_messages[0].file.name == f"{marker}.txt"

    hidden_messages = [message for message in messages if message.message == f"{marker} hidden"]
    assert len(hidden_messages) == 1
    assert getattr(hidden_messages[0], "fwd_from", None) is None

    exec_messages = [message for message in messages if f"{marker} encyclo" in (message.message or "")]
    assert len(exec_messages) == 1
    assert "<1 media was removed>" in exec_messages[0].message
    assert exec_messages[0].media is None
    assert getattr(exec_messages[0], "fwd_from", None) is None


def test_all_messages(clean_state):
    runtime = clean_state
    marker = f"case-{uuid.uuid4().hex[:8]}"
    profiles = [
        {
            "_id": "all_messages",
            "name": "all_messages",
            "channels": [runtime["input_channel"]],
            "output": [
                {
                    "all_messages": True,
                    "ex_rules": [
                        {
                            "regex": rf"{marker} skip"
                        },
                        {
                            "eval": "msg.sticker"
                        },
                    ],
                    "output_channel": runtime["output_channel"],
                }
            ],
        }
    ]
    prepare_profiles(runtime["db"], profiles)

    with tempfile.TemporaryDirectory() as tmp_dir:
        folder = Path(tmp_dir)
        album_one = write_file(folder, "album1.png", PNG_BYTES)
        album_two = write_file(folder, "album2.png", PNG_BYTES)
        sticker = write_file(folder, "sticker.webp", WEBP_BYTES)

        runtime["client"].send_message(runtime["input_channel"], f"{marker} plain")
        runtime["client"].send_message(runtime["input_channel"], f"{marker} skip")
        runtime["client"].send_file(
            runtime["input_channel"],
            [album_one, album_two],
            caption=[f"{marker} album 1", f"{marker} album 2"],
            force_document=False,
        )
        send_sticker_or_skip(runtime["client"], runtime["input_channel"], sticker)

        run_profiles_once(runtime["watcher"])

    messages = wait_for_marker_count(
        runtime["client"], runtime["output_channel"], marker, expected_count=3
    )
    texts = [message.message for message in messages]

    assert f"{marker} plain" in texts
    assert f"{marker} skip" not in texts

    album_messages = [message for message in messages if "album" in (message.message or "")]
    assert len(album_messages) == 2
    grouped_ids = {message.grouped_id for message in album_messages}
    assert len(grouped_ids) == 1

    recent_messages = recent_output_messages(runtime["client"], runtime["output_channel"], limit=10)
    sticker_messages = [message for message in recent_messages if getattr(message, "sticker", None)]
    assert not sticker_messages


def test_long_message(clean_state):
    runtime = clean_state
    profiles = [
        {
            "_id": "long_message",
            "name": "long_message",
            "channels": [runtime["input_channel"]],
            "output": [
                {
                    "rules": [
                        {
                            "regex": "."
                        }
                    ],
                    "any_matching": True,
                    "hide_forward": True,
                    "output_channel": runtime["output_channel"],
                }
            ],
        }
    ]
    prepare_profiles(runtime["db"], profiles)

    # https://t.me/varlamov_news/70109
    long_msg = runtime["client"].get_messages("varlamov_news", min_id=70108, max_id=70110)
    msg_id = long_msg[0].forward_to(runtime["input_channel"]).id
    channel_id = str(runtime["input_channel"]).replace('-100', '')
        
    run_profiles_once(runtime["watcher"])

    messages = recent_output_messages(runtime["client"], runtime["output_channel"], limit=10)

    assert len(messages) == 1
    assert f't.me/c/{channel_id}/{msg_id}' in messages[0].message
