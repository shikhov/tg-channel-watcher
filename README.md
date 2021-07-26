# Описание
Приложение отслеживает новые сообщения в каналах Telegram и пересылает те, которые подпадают под ключевые слова. Пример работы: https://t.me/travelekb

# Реализация
Приложение написано на Python 3.7 с использованием фреймворка Telethon. Хранение данных в БД Cloudant (облачный сервис IBM Cloud). Для развертывания прилагается Dockerfile и плейбук Ansible.

# Развертывание
- Создать в IBM Cloud ресурс Cloudant. Создать Service credentials, открыть View credentials, сохранить как `app/src/creds.json`
- Добавить в `app/src/creds.json` ключ `dbname` — имя БД в сервисе Cloudant, в которой будут храниться настройки. Пример:
 ```
 "dbname": "tg-сhannel-watcher",
```
- Создать БД с именем `tg-сhannel-watcher`, в ней создать документ `settings`

- Получить api_id и api_hash для приложения на https://my.telegram.org
- С помощью фреймворка Telethon произвести аутентификацию на сервере Telegram, сохранить string session. См.: https://telethon.readthedocs.io/en/latest/concepts/sessions.html#string-sessions
- Заполнить документ `settings`:
    - `profiles` — список профилей с настройками каналов (имя профиля = имя документа в БД)
    - `sleeptimer` — период опроса каналов в секундах
    - `api_id`, `api_hash`, `session` —  значения, полученные на предыдущих шагах

Пример:
```
"profiles": [
    "travelekb"
  ],
  "sleeptimer": 300,
  "api_id": 123456,
  "api_hash": "your_api_hash",
  "session": "your_string_session"
```

- Создать и заполнить документы профилей:
    - `channels` — список имен Telegram-каналов и номер последнего сообщения, с которого начинать отслеживание. Чтобы начать с последнего, указать 0
    - `keywords` — ключевые слова (могут быть регулярными выражениями)
    - `any_matching` — true, если достаточно совпадения одного из ключевых слов; false, если совпадения должны быть по всем
    - `output_channel` — имя целевого канала, куда будут пересылаться сообщения

Пример профиля travelekb:
```
"channels": {
    "piratesru": 0,
    "vandroukiru": 0,
    "turs_sale": 0
  },
  "keywords": [
    "екатеринбург",
    "ебург",
    "екб"
  ],
  "any_matching": true,
  "output_channel": "travelekb",
```
- Установить Docker вручную или через плейбук `ansible/aws2_install_docker.yml` (написан для образа Amazon Linux 2 AMI)
- Развернуть Dockerfile вручную или через плейбук `ansible/rebuild_container.yml`