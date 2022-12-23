# Описание
Приложение отслеживает новые сообщения в каналах Telegram и пересылает те, которые подпадают под ключевые слова. Пример работы: https://t.me/travelekb

# Реализация
Приложение написано на Python 3.7 с использованием фреймворка Telethon. Хранение данных в MongoDB. Для развертывания прилагается Dockerfile и плейбук Ansible.

# Развертывание

- Заполнить `config.py`:
  - `DBANME` — имя БД
  - `CONNSTRING` — строка соединения с MongoDB
- Получить api_id и api_hash для приложения на https://my.telegram.org
- С помощью фреймворка Telethon произвести аутентификацию на сервере Telegram, сохранить string session. См.: https://telethon.readthedocs.io/en/latest/concepts/sessions.html#string-sessions
- Создать БД с именем, указанным в `config.py`, в ней создать коллекции settings и profiles
- В коллекции settings создать документ с _id: "settings" и заполнить его:
    - `profiles` — список профилей с настройками каналов
    - `sleeptimer` — период опроса каналов в секундах
    - `api_id`, `api_hash`, `session` —  значения, полученные на предыдущих шагах

Пример:
```
"_id": "settings",
"profiles": [
    "travelekb"
  ],
  "sleeptimer": 300,
  "api_id": 123456,
  "api_hash": "your_api_hash",
  "session": "your_string_session"
```

- В коллекции profiles создать и заполнить документы профилей:
    - `name` — имя профиля
    - `channels` — список идентификаторов Telegram-каналов и номер последнего сообщения, с которого начинать отслеживание. Чтобы начать с последнего, указать 0
    - `keywords` — ключевые слова (могут быть регулярными выражениями)
    - `any_matching` — true, если достаточно совпадения одного из ключевых слов; false, если совпадения должны быть по всем
    - `output_channel` — идентификатор целевого канала, куда будут пересылаться сообщения
    _Идентификатор канала (или чата) может быть буквенным именем, ссылкой на вступление или числовым ID (см. документацию к Telethon)_

Пример профиля:
```
"name": "travelekb",
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

- Развернуть Dockerfile вручную или через плейбук `ansible/rebuild_container.yml`