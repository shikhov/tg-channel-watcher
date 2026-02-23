import logging
import os
import re
import time
from datetime import datetime, timedelta
from hashlib import md5
from logging import INFO, WARNING, ERROR

from pymongo import MongoClient
from telethon.sync import TelegramClient, types

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
                logger.debug(msg='No new messages')
                continue
            if saved_msg_id == 0:
                logger.debug(msg=f'Init last_msg_id to {last_msg_id}')
                continue
            messages = self.get_messages(self.channel, saved_msg_id, last_msg_id)
            if not messages:
                continue
            logger.debug(msg=f'Got {len(messages)} new messages/albums')
            for output in self.output:
                self.action = Action(output, messages, self.channel)
                self.action.run()

        self.doc['lastupdate'] = str(datetime.now()+timedelta(hours=5))
        db.profiles.update_one({'name' : self.name}, {'$set': self.doc})

    def get_messages(self, channel, saved_msg_id, last_msg_id):
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
        self.filter_dupes = output.get('filter_dupes')
        self.ex_rules = output.get('ex_rules')
        self.messages = messages
        self.channel = channel

    def run(self):
        for self.current_msg in self.messages.values():
            logger.debug(msg=f'Checking msg id {self.current_msg[0].id}...')
            if self.has_to_be_forwarded():
                self.forward_message()

    def has_to_be_forwarded(self):
        if self.all_messages:
            return self.check_ex_rules()
        else:
            return self.check_rules()

    def check_ex_rules(self):
        msg = self.current_msg[0]
        if not self.ex_rules:
            logger.debug(msg='No ex_rules, forwarding message')
            return True
        for ex_rule in self.ex_rules:
            if self.evaluate_rule(ex_rule, msg):
                logger.debug(msg='Message matched ex_rule, skipping')
                return False
            
        logger.debug(msg='Message did not match any ex_rule, forwarding')
        return True

    def check_rules(self):
        matched_count = 0
        for rule in self.rules:
            rule_result = self.evaluate_rule(rule, self.current_msg[0])
            matched_count += rule_result

            if rule_result and self.any_matching:
                return True
            if not rule_result and not self.any_matching:
                return False

        if matched_count == len(self.rules):
            return True

        return False

    def evaluate_rule(self, rule, msg):
        if 'regex' in rule:
            if not msg.message:
                logger.debug(msg='No text, skipping regex rules')
                return False
            rule_regex = rule['regex']
            rule_eval = rule.get('eval')
            debug_info = f"regex /{rule_regex}/{' with eval (' + rule_eval + ')' if rule_eval else ''}"

            if rule_eval:
                for m in re.finditer(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                    try:
                        rule_result = eval(rule_eval)
                    except Exception:
                        logger.error(msg='Error with eval: ' + rule_eval, tg=True, extended=True)
                        break

                    if rule_result:
                        logger.debug(msg=f'{debug_info} - matched')
                        return True
            else:
                if re.search(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                    logger.debug(msg=f'{debug_info} - matched')
                    return True
            
            logger.debug(msg=f'{debug_info} - did not match')
            return False
        
        if 'eval' in rule and 'regex' not in rule:
            rule_eval = rule['eval']
            try:
                rule_result = eval(rule_eval)
            except Exception:
                logger.error(msg='Error with eval: ' + rule_eval, tg=True, extended=True)
                return False

            if rule_result:
                logger.debug(msg=f"eval '{rule_eval}' - matched")
                return True
            else:
                logger.debug(msg=f"eval '{rule_eval}' - did not match")
                return False
        
        logger.warning(msg='No valid rules found')
        return False

    def forward_message(self):
        try:
            if self.all_messages:
                client.forward_messages(self.output_channel, self.current_msg)
                logger.debug(msg=f'Message forwarded to {self.output_channel}, all_messages={self.all_messages}')
                return

            msg = self.current_msg[0]
            
            if self.filter_dupes and msg.message:
                msg_hash = self.calculate_msg_hash(msg)
                if msg_hash in sent:
                    logger.debug(msg='Message found in sent, skipping')
                    return

            if not msg.from_id:
                # message from channel
                if self.hide_forward:
                    self.send_message(msg)
                else:
                    msg.forward_to(self.output_channel)
            else:
                # message from chat
                self.send_message(msg, force_link=True)

            if self.filter_dupes and msg.message:
                sent[msg_hash] = 1

            logger.debug(msg=f'Message forwarded to {self.output_channel}, hide_forward={self.hide_forward}')

        except TypeError as e:
            logger.warning(msg='Error forwarding!\n' + str(e))
        except Exception as e:
            logger.warning(msg='Error forwarding!\n' + str(e), tg=True, extended=True)

    def calculate_msg_hash(self, msg):
        if not msg.from_id:
            foo = f'{self.output_channel}_{msg.message}'.encode('utf-8')
        else:
            foo = f'{msg.from_id.user_id}_{msg.message}'.encode('utf-8')
        return md5(foo).hexdigest()

    def send_message(self, msg, force_link=False):
        self.trim_message(msg, force_link)
        if msg.noforwards:
            logger.debug(msg='Message has protected content, sending as copy')
            self.send_protected_message(msg)
        else:
            client.send_message(self.output_channel, msg)

    def send_protected_message(self, msg):
        if msg.media:            
            path = msg.download_media()
            attributes = []
            video_note = False
            voice_note = False
            supports_streaming = False
            
            if hasattr(msg.media, 'document') and msg.media.document:
                attributes = msg.media.document.attributes
                for attr in attributes:
                    if isinstance(attr, types.DocumentAttributeVideo):
                        supports_streaming = True
                        if attr.round_message:
                            video_note = True
                    elif isinstance(attr, types.DocumentAttributeAudio):
                        if attr.voice:
                            voice_note = True

            client.send_file(
                self.output_channel, 
                path, 
                caption=msg.message,
                attributes=attributes,
                voice_note=voice_note,
                video_note=video_note,
                supports_streaming=supports_streaming,
                formatting_entities=msg.entities,
            )

            if path and os.path.exists(path):
                os.remove(path)
        else:
            client.send_message(self.output_channel, msg.message, formatting_entities=msg.entities)

    def trim_message(self, msg, force_link):
        MAX_LENGTH = 4096
        if msg.photo or msg.video or msg.audio or msg.document:
            MAX_LENGTH = 1024

        if 'joinchat' in self.channel:
            channel_id = str(msg.chat_id).replace('-100', '')
            link = f'\nt.me/c/{channel_id}/{msg.id}\n{self.channel}'
        else:
            link = f'\nt.me/{self.channel}/{msg.id}'
        trim_length = MAX_LENGTH - len(link) - 1

        if force_link:
            if len(msg.raw_text) + len(link) > MAX_LENGTH:
                logger.debug(msg=f'Message too long, trimming to {MAX_LENGTH} chars')
                msg.raw_text = msg.raw_text[0:trim_length] + '…'
            msg.raw_text += link
        elif len(msg.raw_text) > MAX_LENGTH:
            logger.debug(msg=f'Message too long, trimming to {MAX_LENGTH} chars')
            msg.raw_text = msg.raw_text[0:trim_length] + '…'
            msg.raw_text += link

class Logger:
    HASHTAG = '#tgcw'
    debug_mode = False

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

    def debug(self, msg, tg=False, extended=False):
        if self.debug_mode:
            self._log(INFO, msg, tg, extended)

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
API_ID = settings['api_id']
API_HASH = settings['api_hash']
SESSION = settings['session']
logger = Logger(logchatid=settings.get('logchatid'))

client = TelegramClient(MyStringSession(SESSION), API_ID, API_HASH)
client.flood_sleep_threshold = 24 * 60 * 60
client.start()
sent = {}

while True:
    settings = db.settings.find_one({'_id': 'settings'})
    profiles = settings['profiles']
    sleeptimer = settings['sleeptimer']
    logger.debug_mode = settings.get('debug', False)
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


