# Проверка приложения в TikTok: памятка

Что заполнить в приложении на developers.tiktok.com, как записать демонстрационное видео и в каком порядке
отправить приложение на проверку. Адрес панели в примерах — `myqueue1.duckdns.org`.

## 1. Основная информация

| Поле | Что вписать |
|---|---|
| Тип приложения | «Другое» |
| Значок | 1024×1024 PNG — значок панели |
| Название | `Queue of Publications` |
| Описание | `Schedule and publish your videos to your own TikTok accounts with captions, privacy settings and post status tracking.` |
| Условия использования | `https://myqueue1.duckdns.org/terms` |
| Политика конфиденциальности | `https://myqueue1.duckdns.org/privacy` |
| Платформы | только «Веб» |
| URL веб-версии | `https://myqueue1.duckdns.org` |

Домен подтверждён TXT-записью в DuckDNS. Не удаляйте и не меняйте её: TikTok может проверить снова.

## 2. Продукты и разрешения

- **Login Kit**, Redirect URI: `https://myqueue1.duckdns.org/api/oauth/tiktok/callback`, разрешение `user.info.basic`.
- **Content Posting API** с включённым **Direct Post**, разрешение `video.publish`.

Ничего лишнего: каждый добавленный продукт нужно показать в видео. Display API (аналитику) лучше добавить
после одобрения.

## 3. Песочница

До одобрения приложение работает только в песочнице: публиковать можно в аккаунты, добавленные как тестовые,
и только с видимостью «Только я». Для демонстрационного видео этого достаточно.

1. Откройте вкладку «Песочница» и создайте песочницу.
2. Добавьте те же продукты, разрешения и Redirect URI, что в разделе 2.
3. Добавьте свой аккаунт TikTok в тестовые пользователи (Target users).
4. Возьмите Client key и Client secret песочницы.
5. На сервере запустите `sudo bash scripts/install.sh` ещё раз и введите эти ключи (остальные вопросы — Enter).

После одобрения так же замените ключи на боевые из основного приложения.

## 4. Сценарий демонстрационного видео

Записывайте в обычном браузере: в адресной строке должен быть виден `myqueue1.duckdns.org`. Приложение для
Windows не подойдёт — в нём адреса не видно.

1. **Вход.** Откройте `https://myqueue1.duckdns.org`, покажите ссылки на условия и политику внизу, войдите.
2. **Login Kit.** «Аккаунты» → «Подключить аккаунт TikTok» → страница TikTok с названием приложения и
   запрашиваемыми разрешениями → «Разрешить» → аккаунт появился в списке с аватаром и именем.
3. **Видео.** «Видео» → загрузите ролик → дождитесь «Подходит для TikTok».
4. **Content Posting API.** «Новая кампания»:
   - выберите видео и подключённый аккаунт;
   - напишите подпись;
   - **видимость выберите вручную** — покажите, что значения по умолчанию нет; в песочнице — «Только я»;
   - покажите переключатели комментариев, Duet и Stitch (выключены по умолчанию) и отметку коммерческого контента;
   - на экране подтверждения покажите аватар и имя аккаунта, отметьте согласие с Music Usage Confirmation и
     нажмите «Подтвердить и запустить».
5. **Результат.** Страница кампании: статус меняется на «Опубликовано». Откройте TikTok и покажите видео в
   профиле.
6. **Отключение (по желанию).** «Аккаунты» → меню аккаунта → «Отключить от TikTok»: доступ отзывается.

Требования TikTok к файлу: mp4 или mov, до 50 МБ, до 5 файлов. Запись экрана в 1080p за несколько минут легко
превышает 50 МБ — записывайте в 720p, разделите на 2–3 ролика или сожмите программой HandBrake.
Записать экран на Windows: Win + Alt + R (Xbox Game Bar) или OBS Studio.

## 5. Текст для поля «Объясните, как каждый продукт работает»

```
Queue of Publications is a web panel for a small team that publishes its own videos to TikTok accounts whose owners connected them.

Login Kit: on the Accounts page the owner clicks "Connect TikTok account", signs in with TikTok and grants user.info.basic and video.publish. The owner can disconnect at any time; the token is then revoked.

Content Posting API (Direct Post): a team member uploads a video, selects accounts and writes a caption. creator_info is queried before posting: privacy level is chosen manually from the allowed options with no default, Comment/Duet/Stitch are off by default and disabled if the creator does not allow them, commercial content can be disclosed, video length is checked. The confirmation screen shows each creator's avatar and nickname and requires the Music Usage Confirmation. Videos are sent with FILE_UPLOAD and the post status is shown.
```

## 6. Порядок

1. Заполнить разделы 1–2 и сохранить («Сохранить», не «Отправить»).
2. Запустить панель на сервере: `sudo bash scripts/install.sh` (см. README, «Установка на сервер»).
3. Настроить песочницу (раздел 3) и проверить, что аккаунт подключается и видео публикуется.
4. Записать видео (раздел 4), загрузить, вставить текст (раздел 5) и отправить на проверку.
