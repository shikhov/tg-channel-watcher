import logging
import re
import time
from datetime import datetime, timedelta

from pymongo import MongoClient
from telethon.sync import TelegramClient

from session import MyStringSession
from config import CONNSTRING, DBNAME

logging.basicConfig(level=logging.INFO)

db = MongoClient(CONNSTRING).get_database(DBNAME)
settings = db.settings.find_one({'_id': 'settings'})
api_id = settings['api_id']
api_hash = settings['api_hash']
session = settings['session']

tg = TelegramClient(MyStringSession(session), api_id, api_hash)
tg.start()

while True:
    db = MongoClient(CONNSTRING).get_database(DBNAME)
    settings = db.settings.find_one({'_id': 'settings'})
    profiles = settings['profiles']
    sleeptimer = settings['sleeptimer']

    for profile_name in profiles:
        profile = db.profiles.find_one({'name': profile_name})
        channels = profile['channels']
        keywords = profile['keywords']
        output_channel = profile['output_channel']
        any_matching = profile['any_matching']
        for channel in channels:
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
                match = False
                matched_count = 0
                for keyword in keywords:
                    if re.search(keyword, msg.message.lower()):
                        matched_count += 1
                        if any_matching:
                            match = True
                            break
                    elif not any_matching: break

                if match or matched_count == len(keywords):
                    msg.forward_to(output_channel)

        profile['lastupdate'] = str(datetime.now()+timedelta(hours=5))
        db.profiles.update_one({'name' : profile_name}, {'$set': profile})

    db.client.close()
    logging.info(f'Sleeping for {sleeptimer} seconds...')
    time.sleep(sleeptimer)


