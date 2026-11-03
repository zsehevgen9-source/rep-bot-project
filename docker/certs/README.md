# Дополнительные сертификаты

Если MAX API отвечает ошибкой `CERTIFICATE_VERIFY_FAILED` (сертификат выпущен удостоверяющим центром Минцифры),
положите сюда два файла в формате PEM с расширением `.crt` и пересоберите образ (`docker compose up -d --build`):

- `russian_trusted_root_ca_pem.crt` — https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
- `russian_trusted_sub_ca_pem.crt` — https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt

Актуальные файлы и официальная инструкция: https://www.gosuslugi.ru/crt

PowerShell, из корня проекта:

```powershell
curl.exe -o docker/certs/russian_trusted_root_ca_pem.crt https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
curl.exe -o docker/certs/russian_trusted_sub_ca_pem.crt  https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt
```
