import json
import re
import time
from datetime import datetime
from datetime import timedelta

from cloudant.client import Cloudant
from telethon.sessions import StringSession
from telethon.sync import TelegramClient
from telethon.tl.functions.contacts import ResolveUsernameRequest
from telethon.tl.functions.messages import ForwardMessagesRequest

creds = json.load(open('creds.json'))

dbname = creds['dbname']
docname = creds['docname']
dbuser = creds['username']
dbpass = creds['password']
dburl = 'https://' + creds['host']
dbclient = Cloudant(dbuser, dbpass, url=dburl, connect=True)

db = dbclient[dbname]
settings = db['settings']
api_id = settings['api_id']
api_hash = settings['api_hash']
session = settings['session']
output_channel = settings['output_channel']

tg = TelegramClient(StringSession(session), api_id, api_hash)
tg.start()

doc = db[docname]
channels = doc['channels']

peers = {}
for channel in channels:
    peers[channel] = tg(ResolveUsernameRequest(channel))

output_peer = tg(ResolveUsernameRequest(output_channel))

dbclient.disconnect()

while True:
    dbclient.connect()
    db = dbclient[dbname]
    settings = db['settings']
    keywords = settings['keywords']
    sleeptimer = settings['sleeptimer']
    doc = db[docname]
    channels = doc['channels']

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
            for keyword in keywords:
                if re.search(keyword, msg.message.lower()):
                    tg(ForwardMessagesRequest(from_peer=peers[channel], id=[msg.id], to_peer=output_peer))
                    break

    doc['lastupdate'] = str(datetime.now()+timedelta(hours=5))
    doc.save()
    dbclient.disconnect()

    time.sleep(sleeptimer)
