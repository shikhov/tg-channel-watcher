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
    watcher = None

    def trim(self, force_link):
        MAX_LENGTH = 4096
        if self.media and not self.watcher.premium:
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
                self.watcher.debug(msg=f'Message too long, trimming to {MAX_LENGTH} chars')
                self.text = self.text[0:trim_length] + '…'
            self.text += link
        elif len(self.text) > MAX_LENGTH:
            self.watcher.debug(msg=f'Message too long, trimming to {MAX_LENGTH} chars')
            self.text = self.text[0:trim_length] + '…'
            self.text += link

    def send(self, output_channel, force_link=False):
        try:
            self.trim(force_link)
            if self.noforwards:
                self.watcher.debug(msg='Message has protected content, sending as copy')
                self.send_protected_message(output_channel)
            else:
                self.watcher.client.send_message(output_channel, self)
        except Exception as e:
            self.watcher.error(msg=f'Error sending message!\n{e}', tg=True, extended=True)

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

            self.watcher.client.send_file(
                output_channel,
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
            self.watcher.client.send_message(output_channel, self.message, formatting_entities=self.entities)

class Item:
    def __init__(self, messages: List[TMessage], watcher) -> None:
        self.watcher = watcher
        self.messages: List[TMessage] = messages
        self.msg: TMessage = messages[0]
        self.msg_id: int = messages[0].id
        self.count: int = len(messages)
        self.is_msg_from_chat: bool = bool(self.msg.from_id)

    def forward_to(self, output_channel):
        self.watcher.client.forward_messages(output_channel, self.messages)

    def send_to(self, output_channel, force_link=False):
        if self.count == 1:
            self.msg.send(output_channel, force_link=force_link)
        else:
            self.send_album(output_channel, force_link=force_link)

    def send_album(self, output_channel, force_link=False):
        self.watcher.debug(msg=f'Sending album of {self.count} messages')
        paths = []
        messages = []
        formatting_entities = []
        fake_entities = [types.MessageEntityCode(offset=0, length=0)]

        try:
            for msg in self.messages:
                self.watcher.debug(msg=f'Downloading media for msg id {msg.id}...')
                path = msg.download_media()
                paths.append(path)
                messages.append(msg.message)
                formatting_entities.append(msg.entities or fake_entities)                
                msg.trim(force_link=force_link)

            self.watcher.client.send_file(
                output_channel,
                paths,
                caption=messages,
                formatting_entities=formatting_entities
            )
        except Exception as e:
            self.watcher.error(msg=f'Error sending album!\n{e}', tg=True, extended=True)
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
    def __init__(self, doc, watcher) -> None:
        self.watcher = watcher
        self.profile_id = doc['_id']
        self.name = doc['name']
        self.input_channels = doc['channels']
        self.output = doc['output']
        self.count = 0
        self.init_counters()

    def init_counters(self):
        counters_doc = self.watcher.db.counters.find_one({'profile_id': self.profile_id})
        if not counters_doc:
            result = self.watcher.db.counters.insert_one(
                {
                    'profile_id': self.profile_id,
                    'profile_name': self.name,
                    'counters': {str(channel): 0 for channel in self.input_channels},
                }
            )
            counters_doc = self.watcher.db.counters.find_one({'_id': result.inserted_id})
        else:
            counters_doc['counters'] = {str(c): counters_doc['counters'].get(str(c), 0) for c in self.input_channels}
            counters_doc['profile_name'] = self.name

        self.counters_doc = counters_doc
        self.counters = counters_doc['counters']

    def run(self):
        for self.input_channel in self.input_channels:
            self.process_channel()
            sleep(self.watcher.DELAY)            
        self.update_counters()

    def process_channel(self):
        self.action = None
        self.count += 1
        self.watcher.info(msg=f'[{self.name}]{self.input_channel}')
        try:
            last_msg_id = self.watcher.client.get_messages(self.input_channel, limit=1)[0].id
        except Exception as e:
            self.watcher.warning(msg=f'Error receiving messages!\n{e}', tg=True, extended=True)
            return
        saved_msg_id = self.counters[str(self.input_channel)]
        self.counters[str(self.input_channel)] = last_msg_id
        if last_msg_id <= saved_msg_id:
            self.watcher.debug(msg='No new messages')
            return
        if saved_msg_id == 0:
            self.watcher.debug(msg=f'Init last_msg_id to {last_msg_id}')
            return
        items = self.get_messages(saved_msg_id, last_msg_id)
        if not items:
            return
        self.watcher.debug(msg=f'Got {len(items)} new messages/albums')
        for output in self.output:
            self.action = Action(output, items, watcher=self.watcher)
            self.action.run()

    def update_counters(self):
        self.counters_doc['counters'] = self.counters
        self.counters_doc['lastupdate'] = int(time())
        self.watcher.db.counters.update_one({'_id': self.counters_doc['_id']}, {'$set': self.counters_doc})

    def get_messages(self, saved_msg_id, last_msg_id) -> List[Item]:
        albums = {}
        non_album = []

        for msg in reversed(self.watcher.client.get_messages(self.input_channel, limit=last_msg_id-saved_msg_id)):
            if msg.id <= saved_msg_id:
                continue
            msg.__class__ = TMessage
            msg.input_channel = self.input_channel
            msg.watcher = self.watcher
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

        return [Item(messages=x[1], watcher=self.watcher) for x in sorted(msg_array.items())]
    

class Watcher:
    DELAY = 10

    def __init__(self, connstring, dbname) -> None:
        self.telethon_logger = logging.getLogger('telethon')
        self.db = MongoClient(connstring).get_database(dbname)
        settings = self.db.settings.find_one({'_id': 'settings'})
        API_ID = settings['api_id']
        API_HASH = settings['api_hash']
        SESSION = settings['session']
        self._init_logger(logchatid=settings.get('logchatid'))
        self.sent = {}

        if 'counters' not in self.db.list_collection_names():
            self.db.create_collection('counters')
        
        if 'logs' not in self.db.list_collection_names():
            self.db.create_collection('logs', capped=True, size=5 * 1024 * 1024, max=500)

        self._init_client(SESSION, API_ID, API_HASH)

    def _init_logger(self, logchatid):
        self.logger = Logger(logchatid=logchatid, watcher=self)
        self.info = self.logger.info
        self.warning = self.logger.warning
        self.error = self.logger.error
        self.debug = self.logger.debug

    def _init_client(self, SESSION, API_ID, API_HASH):
        self.client = TelegramClient(MyStringSession(SESSION), API_ID, API_HASH)
        self.client.flood_sleep_threshold = 24 * 60 * 60
        self.client.start()
        me = self.client.get_me()
        if me:    
            self.info(msg=f'Logged in as {me.first_name} {me.last_name} ({me.username}), premium: {me.premium}')
        self.premium = me.premium

    def start_polling(self):
        while True:
            self.reread_settings()
            self.run_profiles()
            self.sleep()
    
    def reread_settings(self):
        settings = self.db.settings.find_one({'_id': 'settings'})
        self.sleeptimer = settings['sleeptimer']
        self.logger.debug_mode = settings.get('debug', False)
        self.telethon_logger.setLevel(settings.get('telethon_loglevel', logging.WARNING))

    def run_profiles(self):        
        self.count = 0
        for profile_doc in self.db.profiles.find({'enable': True}):
            profile = Profile(doc=profile_doc, watcher=self)
            self.logger.set_profile(profile)
            profile.run()
            self.count += profile.count

    def sleep(self):
        actualsleep = self.sleeptimer - self.DELAY*self.count if self.sleeptimer - self.DELAY*self.count > 0 else 0
        self.info(msg=f'Sleeping for {actualsleep} seconds...')
        sleep(actualsleep)

class Action:
    def __init__(self, output, items: List[Item], watcher) -> None:
        self.watcher = watcher
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
            self.watcher.debug(msg=f'Checking msg id {self.item.msg_id}...')
            if self.has_to_be_forwarded(self.item):
                self.forward(self.item)

    def has_to_be_forwarded(self, item):
        if self.all_messages:
            return self.check_ex_rules(item)
        else:
            return self.check_rules(item)

    def check_ex_rules(self, item):        
        if not self.ex_rules:
            self.watcher.debug(msg='No ex_rules, forwarding message')
            return True
        for ex_rule in self.ex_rules:
            if self.evaluate_rule(ex_rule, item.msg):
                self.watcher.debug(msg='Message matched ex_rule, skipping')
                return False
            
        self.watcher.debug(msg='Message did not match any ex_rule, forwarding')
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
                self.watcher.debug(msg='No text, skipping regex rules')
                return False
            rule_regex = rule['regex']
            rule_eval = rule.get('eval')
            debug_info = f"regex /{rule_regex}/{' with eval (' + rule_eval + ')' if rule_eval else ''}"

            if rule_eval:
                for m in re.finditer(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                    try:
                        rule_result = eval(rule_eval)
                    except Exception:
                        self.watcher.error(msg='Error with eval: ' + rule_eval, tg=True, extended=True)
                        break

                    if rule_result:
                        self.watcher.debug(msg=f'{debug_info} - matched')
                        return True
            else:
                if re.search(rule_regex, msg.message, re.IGNORECASE | re.DOTALL):
                    self.watcher.debug(msg=f'{debug_info} - matched')
                    return True
            
            self.watcher.debug(msg=f'{debug_info} - did not match')
            return False
        
        if 'eval' in rule and 'regex' not in rule:
            rule_eval = rule['eval']
            try:
                rule_result = eval(rule_eval)
            except Exception:
                self.watcher.error(msg='Error with eval: ' + rule_eval, tg=True, extended=True)
                return False

            if rule_result:
                self.watcher.debug(msg=f"eval '{rule_eval}' - matched")
                return True
            else:
                self.watcher.debug(msg=f"eval '{rule_eval}' - did not match")
                return False
        
        self.watcher.warning(msg='No valid rules found')
        return False

    def forward(self, item: Item):
        try:
            if self.exec and self.exec_code:
                exec_result = 'Success'
                try:
                    exec(self.exec_code)
                except Exception as e:
                    exec_result = 'Failed'
                    self.watcher.error(msg='Error with exec: ' + self.exec_code + '\n' + str(e), tg=True, extended=True)

                item.send_to(self.output_channel, force_link=item.is_msg_from_chat)
                self.watcher.debug(msg=f'Message forwarded to {self.output_channel}, {exec_result=}')
                return

            if self.all_messages:
                item.forward_to(self.output_channel)
                self.watcher.debug(msg=f'Message forwarded to {self.output_channel}, all_messages={self.all_messages}')
                return
            
            if self.filter_dupes and item.msg.message:
                msg_hash = item.calc_msg_hash(self.output_channel)
                if msg_hash in self.watcher.sent:
                    self.watcher.debug(msg='Message found in sent, skipping')
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
                self.watcher.sent[msg_hash] = 1

            self.watcher.debug(msg=f'Message forwarded to {self.output_channel}, hide_forward={self.hide_forward}')

        except TypeError as e:
            self.watcher.warning(msg=f'Error forwarding!\n{e}')
        except Exception as e:
            self.watcher.warning(msg=f'Error forwarding!\n{e}', tg=True, extended=True)


class Logger:
    HASHTAG = '#tgcw'
    debug_mode = False

    def __init__(self, logchatid, watcher) -> None:
        self.logchatid = logchatid
        self.watcher = watcher

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

        try:
            self.watcher.db.logs.insert_one({
                'ts': int(time()),
                'level': level,
                'message': msg
            })
        except Exception as e:
            logging.error(f'Error saving log to database: {e}')

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
            self.watcher.client.send_message(self.logchatid, msg, parse_mode='HTML')
        except Exception as e:
            self.error(e)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    connstring = os.getenv('CONNSTRING') or config.CONNSTRING
    dbname = os.getenv('DBNAME') or config.DBNAME

    watcher = Watcher(connstring, dbname)
    watcher.start_polling()
