# Описание
Приложение отслеживает новые сообщения в каналах Telegram и пересылает те, которые подпадают под ключевые слова. Пример работы: https://t.me/travelekb

# Реализация
Приложение написано на Python 3.7 с использованием фреймворка Telethon. Хранение данных в БД Cloudant (облачный сервис IBM Cloud). Для развертывания прилагается Dockerfile и плейбук Ansible.

# Развертывание
- Создать в IBM Cloud ресурс Cloudant. Создать Service credentials, открыть View credentials, сохранить как `app/src/creds.json`
- Добавить в `app/src/creds.json` ключи `dbname` и `docname` — имя БД и имя документа, в котором будут перечислены каналы. Пример:
 ```
 "dbname": "tgcw",
 "docname": "input_channels",
```
- Создать БД с именем `tgcw`, в ней создать документы `input_channels` и `settings`
- В документе `input_channels` перечислить имена Telegram-каналов и номер последнего сообщения, с которого начинать отслеживание. Чтобы начать с последнего, указать 0. Пример:
```
"channels": {
    "piratesru": 0,
    "vandroukiru": 0,
    "turs_sale": 0
  },
```
- Получить api_id и api_hash для приложения на https://my.telegram.org
- С помощью фреймворка Telethon произвести аутентификацию на сервере Telegram, сохранить string session. См.: https://telethon.readthedocs.io/en/latest/concepts/sessions.html#string-sessions
- Заполнить документ `settings`:
    - `keywords` — ключевые слова (могут быть регулярными выражениями)
    - `sleeptimer` — период опроса каналов в секундах
    - `output_channel` — имя целевого канала, куда будут пересылаться сообщения
    - `api_id`, `api_hash`, `session` —  значения, полученные на предыдущих шагах

Пример:
```
"keywords": [
    "екатеринбург",
    "ебург",
    "екб"
  ],
  "sleeptimer": 300,
  "output_channel": "travelekb",
  "api_id": 123456,
  "api_hash": "your_api_hash",
  "session": "your_string_session"
```
- Установить Docker вручную или через плейбук `ansible/aws2_install_docker.yml` (написан для образа Amazon Linux 2 AMI)
- Развернуть Dockerfile вручную или через плейбук `ansible/rebuild_container.yml`