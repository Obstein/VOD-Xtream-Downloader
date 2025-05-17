# Katalog VOD Downloader dla Unraid + Infuse

To narzędzie umożliwia lokalne pobieranie filmów i seriali z serwera Xtream Codes i odtwarzanie ich przez Infuse na Apple TV.

## Funkcje

- Lista filmów VOD z Xtream Codes API
- Pobieranie filmów jednym kliknięciem
- Automatyczne ponawianie przy błędzie
- Konfiguracja przez zmienne środowiskowe
- Gotowe pod uruchomienie w kontenerze Docker na Unraid

## Uruchomienie

1. Skonfiguruj `docker-compose.yml` ze swoimi danymi Xtream i ścieżkami do Unraid.
2. Uruchom:

```bash
docker-compose up --build -d
```

3. Wejdź w przeglądarce: `http://<adres_unraid>:5000`

## Konfiguracja Infuse

- Dodaj udział SMB z `/mnt/user/media` w Infuse jako nowe źródło.
- Filmy będą automatycznie widoczne w katalogu.

## Zmienne środowiskowe

| Zmienna               | Opis                                  |
|------------------------|----------------------------------------|
| XTREAM_HOST           | Adres API Xtream Codes (np. http://...)|
| XTREAM_PORT           | Port API Xtream (zwykle 8080)         |
| XTREAM_USERNAME       | Login                                 |
| XTREAM_PASSWORD       | Hasło                                 |
| DOWNLOAD_PATH_MOVIES  | Ścieżka zapisu filmów                 |
| DOWNLOAD_PATH_SERIES  | Ścieżka zapisu seriali                |
| RETRY_COUNT           | Ile razy próbować ponownie przy błędzie |

## Wymagania

- Docker i Docker Compose
- Działający Xtream Codes API

---

Projekt w trakcie rozwoju – dodawane będą: wsparcie dla seriali, sezonów, odcinków, `.nfo`, statusy pobierania.
