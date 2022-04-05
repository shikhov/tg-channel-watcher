import json
import re
import os
import time
import logging
from datetime import datetime
from datetime import timedelta

from cloudant.client import Cloudant
from telethon.sessions import StringSession
from telethon.sync import TelegramClient
from cloudant.adapters import Replay429Adapter

from config import DBNAME

logging.basicConfig(level=logging.INFO)

curdir = os.path.dirname(os.path.abspath(__file__))
credsfile = os.path.join(curdir, 'creds.json')
creds = json.load(open(credsfile))
DB_APIKEY = creds['apikey']
DB_URL = creds['url']

def getDb(dbname):
    return Cloudant.iam(None, DB_APIKEY, url=DB_URL, connect=True, adapter=Replay429Adapter(retries=10, initialBackoff=0.1))[dbname]

db = getDb(DBNAME)
settings = db['settings']
api_id = settings['api_id']
api_hash = settings['api_hash']
session = settings['session']

tg = TelegramClient(StringSession(session), api_id, api_hash)
tg.start()

while True:
    db = getDb(DBNAME)
    settings = db['settings']
    profiles = settings['profiles']
    sleeptimer = settings['sleeptimer']

    for profile_name in profiles:
        logging.info(profile_name)
        profile = db[profile_name]
        channels = profile['channels']
        keywords = profile['keywords']
        output_channel = profile['output_channel']
        any_matching = profile['any_matching']
        for channel in channels:
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
        profile.save()

    logging.info(f'Sleeping for {sleeptimer} seconds...')
    time.sleep(sleeptimer)


