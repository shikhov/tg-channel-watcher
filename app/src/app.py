import logging
import os
import re
from typing import List
from time import time, sleep
from hashlib import md5
from logging import INFO, WARNING, ERROR

from pymongo import MongoClient
from telethon.sync import TelegramClient, types

from session import MyStringSession
import config

class TMessage(types.Message):
    input_channel = None

    def trim(self, force_link):
        MAX_LENGTH = 4096
        if self.media and not me.premium:
            MAX_LENGTH = 1024

        channel_id = str(self.chat_id).replace('-100', '')
        if 'joinchat' in str(self.input_channel):
            link = f'\nt.me/c/{channel_id}/{self.id}\n{self.input_channel}'
        elif isinstance(self.input_channel, int):
            link = f'\nt.me/c/{channel_id}/{self.id}'
        else:
            link = f'\nt.me/{self.input_channel}/{self.id}'
        trim_length = MAX_LENGTH - len(link) - 1

        if force_link:
            if len(self.text) + len(link) > MAX_LENGTH:
                logger.debug(msg=f'Message too long, trimming to {MAX_LENGTH} chars')
                self.text = self.text[0:trim_length] + '…'
            self.text += link
        elif len(self.text) > MAX_LENGTH:
            logger.debug(msg=f'Message too long, trimming to {MAX_LENGTH} chars')
            self.text = self.text[0:trim_length] + '…'
            self.text += link

    def send(self, output_channel, force_link=False):
        try:
            self.trim(force_link)
            if self.noforwards:
                logger.debug(msg='Message has protected content, sending as copy')
                self.send_protected_message(output_channel)
            else:
                client.send_message(output_channel, self)
        except Exception as e:
            logger.error(msg=f'Error sending message!\n{e}', tg=True, extended=True)

    def send_protected_message(self, output_channel):
        if self.media:            
            path = self.download_media()
            attributes = []
            video_note = False
            voice_note = False
            supports_streaming = False
            
            if hasattr(self.media, 'document') and self.media.document:
                attributes = self.media.document.attributes
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
                caption=self.message,
                attributes=attributes,
                voice_note=voice_note,
                video_note=video_note,
                supports_streaming=supports_streaming,
                formatting_entities=self.entities,
            )

            if path and os.path.exists(path):
                os.remove(path)
        else:
            client.send_message(output_channel, self.message, formatting_entities=self.entities)

class Item:
    def __init__(self, messages: List[TMessage]) -> None:
        self.messages: List[TMessage] = messages
        self.msg: TMessage = messages[0]
        self.msg_id: int = messages[0].id
        self.count: int = len(messages)
        self.is_msg_from_chat: bool = bool(self.msg.from_id)

    def forward_to(self, output_channel):
        client.forward_messages(output_channel, self.messages)

    def send_to(self, output_channel, force_link=False):
        if self.count == 1:
            self.msg.send(output_channel, force_link=force_link)
        else:
            self.send_album(output_channel, force_link=force_link)

    def send_album(self, output_channel, force_link=False):
        logger.debug(msg=f'Sending album of {self.count} messages')
        paths = []
        messages = []
        formatting_entities = []
        fake_entities = [types.MessageEntityCode(offset=0, length=0)]

        try:
            for msg in self.messages:
                logger.debug(msg=f'Downloading media for msg id {msg.id}...')
                path = msg.download_media()
                paths.append(path)
                messages.append(msg.message)
                formatting_entities.append(msg.entities or fake_entities)                
                msg.trim(force_link=force_link)

            client.send_file(                
                output_channel,
                paths,
                caption=messages,
                formatting_entities=formatting_entities
            )
        except Exception as e:
            logger.error(msg=f'Error sending album!\n{e}', tg=True, extended=True)
        finally:
            for path in paths:
                if path and os.path.exists(path):
                    os.remove(path)

    def calc_msg_hash(self, output_channel):
        if self.is_msg_from_chat:
            foo = f'{self.msg.from_id.user_id}_{self.msg.message}'.encode('utf-8')
        else:
            foo = f'{output_channel}_{self.msg.message}'.encode('utf-8')            
        return md5(foo).hexdigest()


class Profile:
    def __init__(self, doc) -> None:
        self.profile_id = doc['_id']
        self.name = doc['name']
        self.input_channels = doc['channels']
        self.output = doc['output']
        self.count = 0
        self.init_counters()

    def init_counters(self):
        counters_doc = db.counters.find_one({'profile_id': self.profile_id})
        if not counters_doc:
            result = db.counters.insert_one(
                {
                    'profile_id': self.profile_id,
                    'profile_name': self.name,
                    'counters': {str(channel): 0 for channel in self.input_channels},
                }
            )
            counters_doc = db.counters.find_one({'_id': result.inserted_id})
        else:
            counters_doc['counters'] = {str(c): counters_doc['counters'].get(str(c), 0) for c in self.input_channels}
            counters_doc['profile_name'] = self.name

        self.counters_doc = counters_doc
        self.counters = counters_doc['counters']

    def run(self):
        for self.input_channel in self.input_channels:
            self.process_channel()
            sleep(DELAY)            
        self.update_counters()

    def process_channel(self):
        self.action = None
        self.count += 1
        logger.info(msg=f'[{self.name}]{self.input_channel}')
        try:
            last_msg_id = client.get_messages(self.input_channel, limit=1)[0].id
        except Exception as e:
            logger.warning(msg=f'Error receiving messages!\n{e}', tg=True, extended=True)
            return
        saved_msg_id = self.counters[str(self.input_channel)]
        self.counters[str(self.input_channel)] = last_msg_id
        if last_msg_id <= saved_msg_id:
            logger.debug(msg='No new messages')
            return
        if saved_msg_id == 0:
            logger.debug(msg=f'Init last_msg_id to {last_msg_id}')
            return
        items = self.get_messages(saved_msg_id, last_msg_id)
        if not items:
            return
        logger.debug(msg=f'Got {len(items)} new messages/albums')
        for output in self.output:
            self.action = Action(output, items)
            self.action.run()

    def update_counters(self):
        self.counters_doc['counters'] = self.counters
        self.counters_doc['lastupdate'] = int(time())
        db.counters.update_one({'_id': self.counters_doc['_id']}, {'$set': self.counters_doc})

    def get_messages(self, saved_msg_id, last_msg_id) -> List[Item]:
        albums = {}
        non_album = []

        for msg in reversed(client.get_messages(self.input_channel, limit=last_msg_id-saved_msg_id)):            
            if msg.id <= saved_msg_id:
                continue
            msg.__class__ = TMessage
            msg.input_channel = self.input_channel
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

        return [Item(messages=x[1]) for x in sorted(msg_array.items())]


class Action:
    def __init__(self, output, items: List[Item]) -> None:
        self.output_channel = output['output_channel']
        self.rules = output.get('rules')
        self.any_matching = output.get('any_matching')
        self.hide_forward = output.get('hide_forward')
        self.all_messages = output.get('all_messages')
        self.filter_dupes = output.get('filter_dupes')
        self.ex_rules = output.get('ex_rules')
        self.exec = output.get('exec')
        self.exec_code = output.get('exec_code')
        self.items = items

    def run(self):
        for self.item in self.items:
            logger.debug(msg=f'Checking msg id {self.item.msg_id}...')
            if self.has_to_be_forwarded(self.item):
                self.forward(self.item)

    def has_to_be_forwarded(self, item):
        if self.all_messages:
            return self.check_ex_rules(item)
        else:
            return self.check_rules(item)

    def check_ex_rules(self, item):        
        if not self.ex_rules:
            logger.debug(msg='No ex_rules, forwarding message')
            return True
        for ex_rule in self.ex_rules:
            if self.evaluate_rule(ex_rule, item.msg):
                logger.debug(msg='Message matched ex_rule, skipping')
                return False
            
        logger.debug(msg='Message did not match any ex_rule, forwarding')
        return True

    def check_rules(self, item):
        matched_count = 0
        for rule in self.rules:
            rule_result = self.evaluate_rule(rule, item.msg)
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

    def forward(self, item: Item):
        try:
            if self.exec and self.exec_code:
                exec_result = 'Success'
                try:
                    exec(self.exec_code)
                except Exception as e:
                    exec_result = 'Failed'
                    logger.error(msg='Error with exec: ' + self.exec_code + '\n' + str(e), tg=True, extended=True)

                item.send_to(self.output_channel, force_link=item.is_msg_from_chat)
                logger.debug(msg=f'Message forwarded to {self.output_channel}, {exec_result=}')
                return

            if self.all_messages:
                item.forward_to(self.output_channel)
                logger.debug(msg=f'Message forwarded to {self.output_channel}, all_messages={self.all_messages}')
                return
            
            if self.filter_dupes and item.msg.message:
                msg_hash = item.calc_msg_hash(self.output_channel)
                if msg_hash in sent:
                    logger.debug(msg='Message found in sent, skipping')
                    return

            if item.is_msg_from_chat:
                # message from chat, always append link
                item.send_to(self.output_channel, force_link=True)                
            else:
                # message from channel
                if self.hide_forward:
                    item.send_to(self.output_channel)
                else:
                    item.forward_to(self.output_channel)

            if self.filter_dupes and item.msg.message:
                sent[msg_hash] = 1

            logger.debug(msg=f'Message forwarded to {self.output_channel}, hide_forward={self.hide_forward}')

        except TypeError as e:
            logger.warning(msg=f'Error forwarding!\n{e}')
        except Exception as e:
            logger.warning(msg=f'Error forwarding!\n{e}', tg=True, extended=True)


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
            header += f'Channel: {self.profile.input_channel}\n'
            if self.profile.action:
                header += f'MsgID: {self.profile.action.item.msg_id}\n'
                header += f'Output Channel: {self.profile.action.output_channel}\n'
            header += '</pre>'
        msg = header + icon + msg + f'\n{self.HASHTAG}'

        try:
            client.send_message(self.logchatid, msg, parse_mode='HTML')
        except Exception as e:
            self.error(e)


DELAY = 10

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    telethon_logger = logging.getLogger('telethon')

    connstring = os.getenv('CONNSTRING') or config.CONNSTRING
    dbname = os.getenv('DBNAME') or config.DBNAME
    
    db = MongoClient(connstring).get_database(dbname)
    settings = db.settings.find_one({'_id': 'settings'})
    API_ID = settings['api_id']
    API_HASH = settings['api_hash']
    SESSION = settings['session']
    logger = Logger(logchatid=settings.get('logchatid'))
    sent = {}

    if 'counters' not in db.list_collection_names():
        db.create_collection('counters')

    client = TelegramClient(MyStringSession(SESSION), API_ID, API_HASH)
    client.flood_sleep_threshold = 24 * 60 * 60
    client.start()
    global me
    me = client.get_me()    
    if me:    
        logger.info(msg=f'Logged in as {me.first_name} {me.last_name} ({me.username}), premium: {me.premium}')

    while True:
        settings = db.settings.find_one({'_id': 'settings'})
        sleeptimer = settings['sleeptimer']
        logger.debug_mode = settings.get('debug', False)
        telethon_logger.setLevel(settings.get('telethon_loglevel', logging.WARNING))
        count = 0

        for profile_doc in db.profiles.find({'enable': True}):
            profile = Profile(doc=profile_doc)
            logger.set_profile(profile)
            profile.run()
            count += profile.count

        actualsleep = sleeptimer - DELAY*count if sleeptimer - DELAY*count > 0 else 0
        logger.info(msg=f'Sleeping for {actualsleep} seconds...')
        sleep(actualsleep)