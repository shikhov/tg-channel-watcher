import logging
import re
import time
from datetime import datetime, timedelta
from hashlib import md5
from logging import INFO, WARNING, ERROR

from pymongo import MongoClient
from telethon.sync import TelegramClient

from session import MyStringSession
from config import CONNSTRING, DBNAME

class Profile:
    def __init__(self, doc) -> None:
        self.doc = doc
        self.name = doc['name']
        self.channels = doc['channels']
        self.output = doc['output']
        self.count = 0

    def process(self):
        for self.channel in self.channels:
            time.sleep(DELAY)
            self.action = None
            self.count += 1
            logger.info(msg=f'[{self.name}]{self.channel}')
            try:
                last_msg_id = client.get_messages(self.channel, limit=1)[0].id
            except Exception as e:
                logger.warning(msg=f'Error receiving messages!\n{e}', tg=True, extended=True)
                continue
            saved_msg_id = self.channels[self.channel]
            self.doc['channels'][self.channel] = last_msg_id
            if last_msg_id <= saved_msg_id:
                continue
            if saved_msg_id == 0:
                continue
            messages = self.getMessages(self.channel, saved_msg_id, last_msg_id)
            if not messages:
                continue
            for output in self.output:
                self.action = Action(output, messages, self.channel)
                self.action.run()

        self.doc['lastupdate'] = str(datetime.now()+timedelta(hours=5))
        db.profiles.update_one({'name' : self.name}, {'$set': self.doc})


    def getMessages(self, channel, saved_msg_id, last_msg_id):
        albums = {}
        non_album = []

        for msg in reversed(client.get_messages(channel, limit=last_msg_id-saved_msg_id)):
            if msg.id <= saved_msg_id:
                continue
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

        return dict(sorted(msg_array.items()))


class Action:
    def __init__(self, output, messages, channel) -> None:
        self.output_channel = output['output_channel']
        self.rules = output.get('rules')
        self.any_matching = output.get('any_matching')
        self.hide_forward = output.get('hide_forward')
        self.all_messages = output.get('all_messages')
        self.ex_rules = output.get('ex_rules')
        self.messages = messages
        self.channel = channel

    def run(self):
        if self.all_messages:
            self.processAllMessages()
        else:
            self.processRules()

    def processAllMessages(self):
        for self.current_msg in self.messages.values():
            if self.checkExRules():
                self.forwardMessage()

    def checkExRules(self):
        msg = self.current_msg[0]
        if not msg.message:
            return True
        if not self.ex_rules:
            return True
        for ex_rule in self.ex_rules:
            if re.search(ex_rule['regex'], msg.message, re.IGNORECASE | re.DOTALL):
                return False
        return True

    def processRules(self):
        for self.current_msg in self.messages.values():
            if self.checkRules():
                self.forwardMessage()

    def checkRules(self):
        msg = self.current_msg[0]
        if not msg.message:
            return False
        matched_count = 0
        for rule in self.rules:
            rule_regex = rule['regex']
            rule_eval = rule.get('eval')
            rule_result = False
            if rule_eval:
                for m in re.finditer(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                    try:
                        rule_result = eval(rule_eval)
                    except Exception:
                        logger.error(msg='Error with eval: ' + rule_eval, tg=True, extended=True)
                        break

                    if rule_result:
                        matched_count += 1
                        break
            else:
                if re.search(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                    rule_result = True
                    matched_count += 1

            if rule_result and self.any_matching:
                return True
            if not rule_result and not self.any_matching:
                return False

        if matched_count == len(self.rules):
            return True

        return False


    def forwardMessage(self):
        try:
            if self.all_messages:
                client.forward_messages(self.output_channel, self.current_msg)
            else:
                msg = self.current_msg[0]
                if not msg.from_id:
                    # message from channel
                    foo = f'{self.output_channel}_{msg.message}'.encode('utf-8')
                    msg_hash = md5(foo).hexdigest()
                    if msg_hash in sent:
                        return
                    if self.hide_forward:
                        self.trimMessage(msg, False)
                        client.send_message(self.output_channel, msg)
                    else:
                        msg.forward_to(self.output_channel)
                else:
                    # message from chat
                    foo = f'{msg.from_id.user_id}_{msg.message}'.encode('utf-8')
                    msg_hash = md5(foo).hexdigest()
                    if msg_hash in sent:
                        return
                    self.trimMessage(msg, True)
                    client.send_message(self.output_channel, msg)

                sent[msg_hash] = 1
        except TypeError as e:
            logger.warning(msg='Error forwarding!\n' + str(e))
        except Exception as e:
            logger.warning(msg='Error forwarding!\n' + str(e), tg=True, extended=True)


    def trimMessage(self, msg, always_include_link):
        max_length = 4096
        if msg.photo or msg.video or msg.audio or msg.document:
            max_length = 1024

        if 'joinchat' in self.channel:
            channel_id = str(msg.chat_id).replace('-100', '')
            link = f'\nt.me/c/{channel_id}/{msg.id}\n{self.channel}'
        else:
            link = f'\nt.me/{self.channel}/{msg.id}'
        trim_length = max_length - len(link) - 1

        if always_include_link:
            if len(msg.raw_text) + len(link) > max_length:
                msg.raw_text = msg.raw_text[0:trim_length] + '…'
            msg.raw_text += link
        elif len(msg.raw_text) > max_length:
            msg.raw_text = msg.raw_text[0:trim_length] + '…'
            msg.raw_text += link


class Logger:
    HASHTAG = '#tgcw'

    def __init__(self, logchatid) -> None:
        self.logchatid = logchatid

    def set_profile(self, profile: Profile):
        self.profile = profile

    def info(self, msg, tg=False, extended=False):
        self._log(INFO, msg, tg, extended)

    def warning(self, msg, tg=False, extended=False):
        self._log(WARNING, msg, tg, extended)

    def error(self, msg, tg=False, extended=False):
        self._log(ERROR, msg, tg, extended)

    def _log(self, level, msg, tg, extended):
        if level == INFO:
            logging.info(msg)
            icon = 'ℹ️ '

        if level == WARNING:
            logging.warning(msg)
            icon = '⚠️ '

        if level == ERROR:
            logging.error(msg)
            icon = '⛔️ '

        if not tg:
            return
        if not self.logchatid:
            return

        header = ''
        if extended:
            header += '<pre>'
            header += f'Profile: {self.profile.name}\n'
            header += f'Channel: {self.profile.channel}\n'
            if self.profile.action:
                header += f'MsgID: {self.profile.action.current_msg[0].id}\n'
                header += f'Output Channel: {self.profile.action.output_channel}\n'
            header += '</pre>'
        msg = header + icon + msg + f'\n{self.HASHTAG}'

        try:
            client.send_message(self.logchatid, msg, parse_mode='HTML')
        except Exception as e:
            self.error(e)


DELAY = 10

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

db = MongoClient(CONNSTRING).get_database(DBNAME)
settings = db.settings.find_one({'_id': 'settings'})
api_id = settings['api_id']
api_hash = settings['api_hash']
session = settings['session']
logger = Logger(settings.get('logchatid'))

client = TelegramClient(MyStringSession(session), api_id, api_hash)
client.flood_sleep_threshold = 24 * 60 * 60
client.start()
sent = {}

while True:
    settings = db.settings.find_one({'_id': 'settings'})
    profiles = settings['profiles']
    sleeptimer = settings['sleeptimer']
    count = 0

    for profile in profiles:
        if not profile['enable']:
            continue
        profile_name = profile['name']
        profile_doc = db.profiles.find_one({'name': profile_name})
        if not profile_doc:
            logger.info(msg=f'Profile doc "{profile_name}" not found!', tg=True)
            continue

        p = Profile(doc=profile_doc)
        logger.set_profile(p)
        p.process()
        count += p.count

    actualsleep = sleeptimer - DELAY*count if sleeptimer - DELAY*count > 0 else 0
    logger.info(msg=f'Sleeping for {actualsleep} seconds...')
    time.sleep(actualsleep)


