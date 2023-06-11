import logging
import re
import time
from datetime import datetime, timedelta
from hashlib import md5

from pymongo import MongoClient
from telethon.sync import TelegramClient

from session import MyStringSession
from config import CONNSTRING, DBNAME

def checkMessage():
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

def forwardMessage():
    if not msg.from_id:
        msg.forward_to(output_channel)
        return

    foo = f'{msg.from_id.user_id}_{msg.message}'.encode('utf-8')
    msg_hash = md5(foo).hexdigest()
    if msg_hash not in sent:
        if 'joinchat' not in channel:
            link = f'\nt.me/{channel}/{msg.id}'
        else:
            channel_id = str(msg.chat_id).replace('-100', '')
            link = f'\nt.me/c/{channel_id}/{msg.id}\n{channel}'
        max_length = 4096
        if msg.photo or msg.video or msg.audio or msg.document:
            max_length = 1024
        if len(msg.raw_text) + len(link) > max_length:
            trim_length = max_length - len(link) - 1
            msg.raw_text = msg.raw_text[0:trim_length] + '…'
        msg.raw_text += link
        tg.send_message(output_channel, msg)

    sent[msg_hash] = 1


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

    for profile in profiles:
        if not profile['enable']: continue
        profile_name = profile['name']
        profile_doc = db.profiles.find_one({'name': profile_name})
        channels = profile_doc['channels']
        rules = profile_doc['rules']
        output_channel = profile_doc['output_channel']
        any_matching = profile_doc['any_matching']
        for channel in channels:
            time.sleep(10)
            logging.info(f'[{profile_name}]{channel}')
            saved_msg_id = channels[channel]
            last_msg_id = tg.get_messages(channel, limit=1)[0].id
            channels[channel] = last_msg_id
            if saved_msg_id == 0: continue
            if last_msg_id <= saved_msg_id: continue
            new_msg_count = last_msg_id - saved_msg_id
            for msg in reversed(tg.get_messages(channel, limit=new_msg_count)):
                if not msg.message: continue
                if msg.id <= saved_msg_id: continue
                if checkMessage(): forwardMessage()

        profile_doc['lastupdate'] = str(datetime.now()+timedelta(hours=5))
        db.profiles.update_one({'name' : profile_name}, {'$set': profile_doc})

    db.client.close()
    logging.info(f'Sleeping for {sleeptimer} seconds...')
    time.sleep(sleeptimer)


