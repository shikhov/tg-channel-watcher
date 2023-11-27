import logging
import re
import time
from datetime import datetime, timedelta
from hashlib import md5

from pymongo import MongoClient
from telethon.sync import TelegramClient

from session import MyStringSession
from config import CONNSTRING, DBNAME

def processRules():
    for msg_id in sorted(messages):
        msg = messages[msg_id][0]
        if checkMessage(msg):
            forwardMessage(msg)


def processAllMessages():
    for msg_id in sorted(messages):
        try:
            tg.forward_messages(output_channel, messages[msg_id])
        except Exception:
            pass


def getMessages():
    albums = {}
    non_album = []

    new_msg_count = last_msg_id - saved_msg_id
    for msg in reversed(tg.get_messages(channel, limit=new_msg_count)):
        if msg.id <= saved_msg_id: continue
        if msg.grouped_id:
            if msg.grouped_id in albums:
                albums[msg.grouped_id].append(msg)
            else:
                albums[msg.grouped_id] = [msg]
        else:
            non_album.append(msg)

    msg_array = {}
    for album in albums.values():
        msg_array[album[0].id] = album
    for msg in non_album:
        msg_array[msg.id] = [msg]

    return msg_array


def checkMessage(msg):
    if not msg.message: return False

    matched_count = 0
    for rule in rules:
        rule_regex = rule['regex']
        rule_eval = rule.get('eval')
        rule_result = False
        if rule_eval:
            for m in re.finditer(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                try:
                    rule_result = eval(rule_eval)
                except Exception:
                    logging.info('Error with eval: ' + rule_eval)
                    break

                if rule_result:
                    matched_count += 1
                    break
        else:
            if re.search(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                rule_result = True
                matched_count += 1

        if rule_result and any_matching:
            return True
        if not rule_result and not any_matching:
            return False

    if matched_count == len(rules):
        return True

    return False


def forwardMessage(msg):
    # message from channel
    if not msg.from_id:
        if hide_forward:
            trimMessage(msg, False)
            tg.send_message(output_channel, msg)
        else:
            msg.forward_to(output_channel)
        return

    # message from chat
    foo = f'{msg.from_id.user_id}_{msg.message}'.encode('utf-8')
    msg_hash = md5(foo).hexdigest()
    if msg_hash not in sent:
        trimMessage(msg, True)
        tg.send_message(output_channel, msg)
        sent[msg_hash] = 1


def trimMessage(msg, always_include_link):
    max_length = 4096
    if msg.photo or msg.video or msg.audio or msg.document:
        max_length = 1024

    if 'joinchat' in channel:
        channel_id = str(msg.chat_id).replace('-100', '')
        link = f'\nt.me/c/{channel_id}/{msg.id}\n{channel}'
    else:
        link = f'\nt.me/{channel}/{msg.id}'
    trim_length = max_length - len(link) - 1

    if always_include_link:
        if len(msg.raw_text) + len(link) > max_length:
            msg.raw_text = msg.raw_text[0:trim_length] + '…'
        msg.raw_text += link
    elif len(msg.raw_text) > max_length:
        msg.raw_text = msg.raw_text[0:trim_length] + '…'
        msg.raw_text += link

DELAY = 10

logging.basicConfig(level=logging.INFO)

db = MongoClient(CONNSTRING).get_database(DBNAME)
settings = db.settings.find_one({'_id': 'settings'})
api_id = settings['api_id']
api_hash = settings['api_hash']
session = settings['session']

tg = TelegramClient(MyStringSession(session), api_id, api_hash)
tg.start()
sent = {}

while True:
    db = MongoClient(CONNSTRING).get_database(DBNAME)
    settings = db.settings.find_one({'_id': 'settings'})
    profiles = settings['profiles']
    sleeptimer = settings['sleeptimer']
    i = 0

    for profile in profiles:
        if not profile['enable']: continue
        profile_name = profile['name']
        profile_doc = db.profiles.find_one({'name': profile_name})
        if not profile_doc:
            logging.warning(f'Profile doc "{profile_name}" not found!')
            continue

        channels = profile_doc['channels']
        output = profile_doc['output']
        for channel in channels:
            time.sleep(DELAY)
            i += 1
            logging.info(f'[{profile_name}]{channel}')
            try:
                last_msg_id = tg.get_messages(channel, limit=1)[0].id
            except Exception:
                logging.error('Error receiving messages!')
                continue
            saved_msg_id = channels[channel]
            channels[channel] = last_msg_id
            if saved_msg_id == 0: continue
            if last_msg_id <= saved_msg_id: continue
            messages = getMessages()
            for entry in output:
                output_channel = entry['output_channel']
                rules = entry.get('rules')
                any_matching = entry.get('any_matching')
                hide_forward = entry.get('hide_forward')
                all_messages = entry.get('all_messages')

                if all_messages:
                    processAllMessages()
                else:
                    processRules()

        profile_doc['lastupdate'] = str(datetime.now()+timedelta(hours=5))
        db.profiles.update_one({'name' : profile_name}, {'$set': profile_doc})

    db.client.close()
    actualsleep = sleeptimer - DELAY*i if sleeptimer - DELAY*i >=0 else 0
    logging.info(f'Sleeping for {actualsleep} seconds...')
    time.sleep(actualsleep)


